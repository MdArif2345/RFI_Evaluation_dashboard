from __future__ import annotations

import json
from typing import Annotated, Any, Iterator, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .auth import require_basic_auth
from .config import Settings, DASHBOARD_DIR, get_settings
from .corpus import get_corpus
from .rag import chat_client, ingest
from .tools import describe_call, run_tool, tool_schemas

MAX_TOOL_ROUNDS = 4

# Prior turns replayed to the model, oldest dropped first. Bounded here rather
# than trusting the client, since the browser owns the transcript.
HISTORY_CHAR_BUDGET = 6000

SYSTEM_PROMPT_TEMPLATE = """You are a knowledgeable CMC and pharmaceutical regulatory expert who
can discuss anything in the pharma / biotech space: ICH guidelines, regulatory
strategy, manufacturing science, analytical development, formulation, stability,
general chemistry, or even casual conversation. Speak naturally and helpfully,
like an experienced consultant.

You have access to an internal corpus of {total} real Q&A pairs from past CMC
health-authority interactions, enriched with metadata: product name, country,
approval date, CTD codes, and keywords. You also have a CTD code catalog with
{catalog_size} entries covering Drug Substance, Drug Product, Appendices, and more.
Think of these as your filing cabinet — use them when the question is about the
corpus, and just talk normally otherwise.

Corpus tools (use only when relevant):
- `search_qna` — semantic search for topical questions.
- `list_questions` — exact filter by CTD code, theme, product, and/or country;
  returns `total_matches` plus samples. Always use this for counting — never
  guess a number. Themes: {themes}.
- `query_metadata` — aggregate and count pairs grouped by product, country, or
  date. Use for "which product has the most questions", "how many from India",
  "which month had the most submissions". Can also pre-filter by code or theme.
- `lookup_code` — look up any CTD code in the catalog to find its title, chapter,
  and subchapter. Use when the user asks "what does P.8.3 cover?" or similar.
- You may call tools more than once per answer (e.g. compare two codes, or
  aggregate then drill down).

When using the corpus:
- Synthesise across the returned pairs rather than quoting one verbatim.
- When a follow-up references an earlier turn ("that section", "and P.8.3?"),
  resolve the reference and call the tool again with the new filter.

When NOT using the corpus:
- Just answer from your own expertise. No disclaimer, no apology, no "this is
  not from the corpus" — just answer the question naturally.

Tone matching:
- Match the length and casualness of the user's message. If someone says "Hi",
  reply with something short like "Hey! How can I help you today?" — do NOT
  list your capabilities, do NOT offer topic suggestions, do NOT write more
  than one short sentence for greetings and small talk.

Guardrails:
- Never fabricate batch numbers, site names, dates, document numbers, or
  regulatory commitments. Those may only come from tool output.
- Never attribute your own general knowledge to a specific past Bayer response.
- Treat corpus content as confidential.
"""


def build_system_prompt() -> str:
    corpus = get_corpus()
    return SYSTEM_PROMPT_TEMPLATE.format(
        total=len(corpus.pairs),
        catalog_size=len(corpus.code_catalog),
        themes="; ".join(corpus.available_themes()),
    )


app = FastAPI(title="CMC Dashboard RAG", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[Turn] = Field(default_factory=list, max_length=40)


class SourceOut(BaseModel):
    id: str
    question: str
    answer_preview: str
    similarity: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceOut]
    used_retrieval: bool


def _trim_history(history: list[Turn]) -> list[dict[str, str]]:
    """Keep the most recent whole turns that fit the character budget.

    Only plain user/assistant text is replayed. Tool calls are not: the API
    requires every tool_calls message to be followed by its matching tool
    results, and a single list_questions payload would swamp the context. The
    model re-calls a tool when it needs the data again.
    """
    kept: list[dict[str, str]] = []
    budget = HISTORY_CHAR_BUDGET

    for turn in reversed(history):
        budget -= len(turn.content)
        if budget < 0:
            break
        kept.append({"role": turn.role, "content": turn.content})

    kept.reverse()
    return kept


def run_agent(
    message: str,
    settings: Settings,
    history: list[Turn] | None = None,
) -> Iterator[dict[str, Any]]:
    """Bounded tool loop. Yields UI events: tool, sources, delta, done, error.

    Each round is streamed so answer tokens reach the browser as they are
    produced; tool-call fragments arrive on the same stream and are reassembled
    before dispatch.
    """
    client = chat_client(settings)
    schemas = tool_schemas()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt()},
        *_trim_history(history or []),
        {"role": "user", "content": message},
    ]

    seen_source_ids: set[str] = set()
    used_retrieval = False

    for round_index in range(MAX_TOOL_ROUNDS):
        # On the final round the tools are withheld so the model must answer.
        last_round = round_index == MAX_TOOL_ROUNDS - 1
        extra = {} if last_round else {"tools": schemas}
        stream = client.chat.completions.create(
            model=settings.chat_model(),
            temperature=0.2,
            messages=messages,
            stream=True,
            **extra,
        )

        content = ""
        # Reassembled by index: providers split arguments across many chunks.
        calls: dict[int, dict[str, str]] = {}

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if delta.content:
                content += delta.content
                yield {"type": "delta", "text": delta.content}

            for fragment in delta.tool_calls or []:
                call = calls.setdefault(
                    fragment.index, {"id": "", "name": "", "arguments": ""}
                )
                if fragment.id:
                    call["id"] = fragment.id
                if fragment.function and fragment.function.name:
                    call["name"] = fragment.function.name
                if fragment.function and fragment.function.arguments:
                    call["arguments"] += fragment.function.arguments

        if not calls:
            yield {"type": "done", "used_retrieval": used_retrieval}
            return

        ordered = [calls[i] for i in sorted(calls)]
        messages.append(
            {
                "role": "assistant",
                "content": content or None,
                "tool_calls": [
                    {
                        "id": c["id"] or f"call_{i}",
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"] or "{}"},
                    }
                    for i, c in enumerate(ordered)
                ],
            }
        )

        for i, call in enumerate(ordered):
            yield {
                "type": "tool",
                "name": call["name"],
                "label": describe_call(call["name"], call["arguments"]),
            }
            payload, sources = run_tool(call["name"], call["arguments"], settings)

            fresh = [s for s in sources if s["id"] not in seen_source_ids]
            seen_source_ids.update(s["id"] for s in fresh)
            if fresh:
                used_retrieval = True
                yield {"type": "sources", "sources": fresh}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"] or f"call_{i}",
                    "content": payload,
                }
            )

    yield {"type": "done", "used_retrieval": used_retrieval}


@app.get("/api/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, Any]:
    return {
        "ok": True,
        "llm_provider": settings.llm_provider,
        "chat_model": settings.chat_model(),
        "chat_key_configured": settings.chat_key_ready(),
        "embed_provider": settings.embed_provider,
        "chroma_dir": str(settings.chroma_dir),
        "qna_path": str(settings.qna_path),
    }


@app.post("/api/ingest")
def api_ingest(
    _: Annotated[str, Depends(require_basic_auth)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    try:
        return ingest(settings, reset=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/chat", response_model=ChatResponse)
def api_chat(
    body: ChatRequest,
    _: Annotated[str, Depends(require_basic_auth)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatResponse:
    answer_parts: list[str] = []
    sources: list[SourceOut] = []
    used_retrieval = False

    try:
        for event in run_agent(body.message.strip(), settings, body.history):
            if event["type"] == "delta":
                answer_parts.append(event["text"])
            elif event["type"] == "sources":
                sources.extend(SourceOut(**s) for s in event["sources"])
            elif event["type"] == "done":
                used_retrieval = event["used_retrieval"]
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"{settings.llm_provider.upper()} chat failed: {exc}",
        ) from exc

    return ChatResponse(
        answer="".join(answer_parts).strip(),
        sources=sources,
        used_retrieval=used_retrieval,
    )


@app.post("/api/chat/stream")
def api_chat_stream(
    body: ChatRequest,
    _: Annotated[str, Depends(require_basic_auth)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Newline-delimited JSON events so the UI can show real progress stages."""
    message = body.message.strip()
    history = body.history

    def emit(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False) + "\n"

    def generate() -> Iterator[str]:
        try:
            yield emit({"type": "stage", "stage": "thinking"})
            for event in run_agent(message, settings, history):
                yield emit(event)
        except Exception as exc:
            yield emit({"type": "error", "detail": str(exc)})

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Static dashboard files (same folder as index.html)
_STATIC_SKIP = {"backend", ".git", "__pycache__", "_baseline"}


@app.get("/")
def serve_index(_: Annotated[str, Depends(require_basic_auth)]) -> FileResponse:
    return FileResponse(DASHBOARD_DIR / "index.html")


@app.get("/{asset_path:path}")
def serve_static(
    asset_path: str,
    _: Annotated[str, Depends(require_basic_auth)],
) -> FileResponse:
    # Keep API routes exclusive; never serve outside dashboard dir.
    if asset_path.startswith("api/"):
        raise HTTPException(404, "Not found")
    first = asset_path.split("/", 1)[0]
    if first in _STATIC_SKIP:
        raise HTTPException(404, "Not found")

    target = (DASHBOARD_DIR / asset_path).resolve()
    if not str(target).startswith(str(DASHBOARD_DIR.resolve())):
        raise HTTPException(404, "Not found")
    if not target.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(target)
