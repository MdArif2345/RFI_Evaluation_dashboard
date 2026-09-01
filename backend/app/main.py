from __future__ import annotations

import json
from typing import Annotated, Any, Iterator

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

SYSTEM_PROMPT_TEMPLATE = """You are a CMC regulatory assistant for an internal Bayer dashboard.

You have tools over a corpus of {total} real question/answer pairs from past CMC
health-authority responses. Reason about what the user needs, call tools to find
out, and call them again if the first result is not enough.

Tool use:
- `search_qna` for topical questions where the wording varies.
- `list_questions` for anything involving a CTD code, a theme, a count, or a list.
  Semantic search cannot count, so never estimate a number from search results —
  always read `total_matches` from `list_questions`.
- You may call tools several times in one answer, for example once per code when
  the user compares two sections.

Themes available to `list_questions`: {themes}.

Answering:
- When tool output covers the question, ground the answer in it and synthesise
  across pairs rather than quoting one.
- When the corpus has nothing relevant, just answer from your own CMC regulatory
  knowledge as a normal part of the reply. Do not announce the gap, do not add a
  disclaimer, and do not tell the user to rephrase.
- Never invent batch numbers, site names, dates, document numbers, or regulatory
  commitments; those may only come from tool output.
- Never attribute a general-knowledge statement to a specific past Bayer response.
- Treat all corpus content as confidential CMC regulatory information.
"""


def build_system_prompt() -> str:
    corpus = get_corpus()
    return SYSTEM_PROMPT_TEMPLATE.format(
        total=len(corpus.pairs),
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


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class SourceOut(BaseModel):
    id: str
    question: str
    answer_preview: str
    similarity: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceOut]
    used_retrieval: bool


def run_agent(message: str, settings: Settings) -> Iterator[dict[str, Any]]:
    """Bounded tool loop. Yields UI events: tool, sources, delta, done, error.

    Each round is streamed so answer tokens reach the browser as they are
    produced; tool-call fragments arrive on the same stream and are reassembled
    before dispatch.
    """
    client = chat_client(settings)
    schemas = tool_schemas()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt()},
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
        for event in run_agent(body.message.strip(), settings):
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

    def emit(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False) + "\n"

    def generate() -> Iterator[str]:
        try:
            yield emit({"type": "stage", "stage": "thinking"})
            for event in run_agent(message, settings):
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
