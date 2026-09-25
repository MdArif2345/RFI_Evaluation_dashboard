from __future__ import annotations

import json
from typing import Annotated, Any, Iterator, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .auth import require_basic_auth
from .chat_db import delete_session, ensure_session, get_analytics, get_session_messages, get_sessions, init_db, save_message
from .config import Settings, DASHBOARD_DIR, get_settings
from .corpus import get_corpus
from .rag import chat_client, ingest
from .tools import describe_call, run_tool, tool_schemas

MAX_TOOL_ROUNDS = 4

# Prior turns replayed to the model, oldest dropped first. Bounded here rather
# than trusting the client, since the browser owns the transcript.
HISTORY_CHAR_BUDGET = 6000

SYSTEM_PROMPT_TEMPLATE = """You are an experienced Regulatory Affairs CMC Manager for prescription
medicinal products for human use. You have deep expertise in CTD Module 3
structure, TRD writing guidelines, pharmaceutical development, drug substance
and drug product manufacturing, quality control, stability, regulatory
assessment questions, and Health Authority deficiency management. You can also
discuss general pharma/biotech topics and have casual conversation.

You have access to an internal corpus of {total} real Q&A pairs from past CMC
health-authority interactions (RFIs, deficiency letters, assessment questions),
enriched with metadata: product name, country, approval date, CTD codes, and
keywords. You also have a CTD code catalog with {catalog_size} entries covering
Drug Substance, Drug Product, Appendices, Regional, and TRD profile codes.
Think of these as your filing cabinet.

**Important**: For ANY substantive CMC, regulatory, or pharma-technical question,
ALWAYS call `search_qna` first to check if the corpus has relevant real-world
examples before answering. Combine corpus results with your own expertise to give
a richer, grounded answer. Only skip tools for casual greetings, small talk, or
clearly non-pharma topics.

Corpus tools:
- `search_qna` — semantic search for topical questions. **Call this by default**
  for any CMC/regulatory question.
- `list_questions` — exact filter by CTD code, theme, product, and/or country;
  returns `total_matches` plus samples. Always use this for counting — never
  guess a number. Themes: {themes}.
- `query_metadata` — aggregate and count pairs grouped by product, country, or
  date. Use for "which product has the most questions", "how many from India",
  "which month had the most submissions". Can also pre-filter by code or theme.
- `lookup_code` — look up any CTD code in the catalog to find its title, chapter,
  and subchapter. Use when the user asks "what does P.8.3 cover?" or similar.
  Also use this to resolve granular TRD profile codes when mapping questions
  hierarchically.
- You may call tools more than once per answer (e.g. compare two codes, or
  aggregate then drill down).

Hierarchical code reasoning:
When reasoning about CTD codes, always think hierarchically:
  1. CTD domain: S (Drug Substance), P (Drug Product), A (Appendices),
     R (Regional information), or Other/Outside Module 3.
  2. High-level CTD section: e.g. S.1, S.4, P.5, P.8, A, R.
  3. CTD subsection: e.g. S.4.1, S.4.5, P.5.1, P.8.3.
  4. Granular TRD profile code: e.g. S.4.1.01, P.5.6.01, P.8.3.06.
Use `lookup_code` to resolve codes to their titles. When answering any
regulatory or deficiency question, always state the CTD mapping at the end of your
response under a "CTD Mapping" heading (domain -> high-level section -> subsection -> TRD code if known).
Before answering, call `lookup_code` to resolve the most applicable code.
If the mapping is uncertain, state the most likely code and note the
uncertainty.

CMC topic classification:
When analysing questions, classify them into one or more of these CMC content
topics: manufacturing process description, control strategy, critical process
parameters, proven acceptable ranges / design space, starting material
justification, impurity control, analytical method validation, specifications
and acceptance criteria, reference standards, batch analysis, stability data,
shelf-life justification, storage conditions, in-use stability, container
closure system, extractables and leachables, microbiological quality, sterility
assurance, pharmaceutical development, formulation justification, comparability,
process validation, regional administrative requirements.

Gap classification for deficiency questions:
When analysing why a Health Authority asked a particular question, consider
which category applies:
  1. **True TRD profile gap** — the current profile does not clearly instruct
     authors to include the content, data, justification, level of detail, or
     regulatory rationale expected by authorities.
  2. **Dossier execution gap** — the TRD profile already requires the information,
     but the submitted dossier did not include it, included it unclearly, or
     placed it in the wrong section.
  3. **Data availability or timing issue** — the authority requested data that
     may not have been mature or available at submission time (e.g. additional
     stability data, validation data, batch data).
  4. **Product-specific issue** — the question arises from a specific molecule,
     formulation, manufacturing process, container closure, impurity, device, or
     regional product situation and should not automatically trigger a general
     TRD profile update.
  5. **Standard Health Authority request** — the authority requested standard data
     or clarification that is already adequately covered by the TRD profile.
  6. **Evolving regulatory expectation** — the question suggests a new or
     increasing expectation from one or more Health Authorities, potentially
     requiring clarification or strengthening of the TRD profile.
Mention the applicable category naturally in your answer when it adds insight —
you do not need to list all six every time.

Conservative decision rules:
- Do not recommend updating a TRD profile merely because a Health Authority
  requested standard information such as additional stability data, batch data,
  specifications, method validation data, or manufacturing clarification, if the
  current TRD profile already clearly requires this information. In such cases,
  classify the issue as dossier execution, data availability, or submission
  timing instead.
- Only suggest a potential TRD profile update when the same or similar question
  recurs across multiple RFIs, products, submissions, or Health Authorities; or
  when the profile is silent, ambiguous, incomplete, outdated, or too high-level
  for the topic; or when the question indicates an emerging or changed regulatory
  expectation.
- Be conservative when recommending changes to controlled TRD profiles. Do not
  overfit one-off authority questions.
- Prefer actionable content-block recommendations over vague statements like
  "add more detail."

When using the corpus:
- Synthesise across the returned pairs rather than quoting one verbatim.
- When a follow-up references an earlier turn ("that section", "and P.8.3?"),
  resolve the reference and call the tool again with the new filter.
- Blend corpus evidence with your own expertise for a comprehensive answer.

When the corpus has no relevant results:
- Just answer from your own expertise. No disclaimer, no apology — just answer
  the question naturally.

Tone matching:
- Use precise RA CMC language when discussing regulatory or technical topics.
- Match the length and casualness of the user's message. If someone says "Hi",
  reply with something short like "Hey! How can I help you today?" — do NOT
  list your capabilities, do NOT offer topic suggestions, do NOT write more
  than one short sentence for greetings and small talk.

Guardrails:
- Never fabricate batch numbers, site names, dates, document numbers, or
  regulatory commitments. Those may only come from tool output.
- Never attribute your own general knowledge to a specific past Bayer response.
- Treat corpus content as confidential.
- Flag uncertainty clearly. If the hierarchical code mapping is uncertain, say so.
- Never invent a profile requirement if the profile text is not provided. If
  profile text is missing, state that the conclusion is provisional.
- Treat your output as decision support for human RA CMC and TRD profile owners,
  not as an automatic approval to change controlled documents.
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


@app.on_event("startup")
def _startup() -> None:
    settings = get_settings()
    init_db(settings.chat_db_path)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[Turn] = Field(default_factory=list, max_length=40)
    session_id: str | None = None
    username: str | None = None


class SourceOut(BaseModel):
    id: str
    question: str
    answer_preview: str
    doc_id: str = ""
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

    # Detect casual / small-talk so we don't force a tool call for them.
    import re as _re
    _msg_lower = message.strip().lower()
    _msg_clean = _re.sub(r"[!.,?]+$", "", _msg_lower)
    _CASUAL_PHRASES = {
        "hi", "hii", "hey", "hello", "yo", "sup",
        "thanks", "thank you", "thank u", "thx",
        "bye", "goodbye", "see you", "see ya",
        "ok", "okay", "sure", "yes", "no", "yep", "nope",
        "good morning", "good evening", "good afternoon", "good night",
        "how are you", "how r u", "what's up", "whats up",
        "i wanna talk", "i want to talk", "let's talk", "lets talk",
        "i wanna talk about something", "i want to talk about something",
        "tell me something", "can we talk", "can we chat",
        "who are you", "what are you", "what can you do",
    }
    _PHARMA_KEYWORDS = _re.compile(
        r"(ctd|cmc|api|drug|product|substance|batch|stability|leachable|"
        r"extractable|specification|impurit|formulation|excipient|"
        r"dissolution|validation|analytical|regulatory|ich|coa|"
        r"p\.\d|s\.\d|m\.\d|shelf.life|container.closure|"
        r"manufacturing|bioequi|pharmacop|assay|method|dossier)",
        _re.IGNORECASE,
    )
    _word_count = len(_msg_lower.split())
    is_casual = (
        _msg_clean in _CASUAL_PHRASES
        or (_word_count <= 6 and not _PHARMA_KEYWORDS.search(_msg_lower))
    )

    for round_index in range(MAX_TOOL_ROUNDS):
        # On the final round the tools are withheld so the model must answer.
        last_round = round_index == MAX_TOOL_ROUNDS - 1
        extra: dict[str, Any] = {}
        if not last_round:
            extra["tools"] = schemas
            if round_index == 0 and not is_casual:
                # Force the model to call a tool on the first round for
                # substantive questions so the source panel always appears.
                extra["tool_choice"] = "required"
            else:
                extra["tool_choice"] = "auto"
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
    message = body.message.strip()

    if body.session_id and body.username:
        ensure_session(body.session_id, body.username)
        save_message(body.session_id, "user", message)

    answer_parts: list[str] = []
    sources: list[SourceOut] = []
    used_retrieval = False

    try:
        for event in run_agent(message, settings, body.history):
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

    answer = "".join(answer_parts).strip()

    if body.session_id and body.username and answer:
        src_dicts = [s.model_dump() for s in sources] if sources else None
        save_message(body.session_id, "assistant", answer, src_dicts)

    return ChatResponse(
        answer=answer,
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
    sid = body.session_id
    uname = body.username

    if sid and uname:
        ensure_session(sid, uname)
        save_message(sid, "user", message)

    def emit(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False) + "\n"

    def generate() -> Iterator[str]:
        answer_parts: list[str] = []
        all_sources: list[dict[str, Any]] = []
        try:
            yield emit({"type": "stage", "stage": "thinking"})
            for event in run_agent(message, settings, history):
                yield emit(event)
                if event["type"] == "delta":
                    answer_parts.append(event["text"])
                elif event["type"] == "sources":
                    all_sources.extend(event.get("sources", []))
        except Exception as exc:
            yield emit({"type": "error", "detail": str(exc)})
            return

        answer = "".join(answer_parts).strip()
        if sid and uname and answer:
            save_message(sid, "assistant", answer, all_sources or None)

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ----- Session & analytics endpoints -----

@app.get("/api/sessions")
def api_sessions(
    username: str,
    _: Annotated[str, Depends(require_basic_auth)],
) -> list[dict[str, Any]]:
    return get_sessions(username)


@app.delete("/api/sessions/{session_id}")
def api_delete_session(
    session_id: str,
    _: Annotated[str, Depends(require_basic_auth)],
) -> dict[str, Any]:
    deleted = delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": True, "session_id": session_id}


@app.get("/api/sessions/{session_id}/messages")
def api_session_messages(
    session_id: str,
    _: Annotated[str, Depends(require_basic_auth)],
) -> list[dict[str, Any]]:
    return get_session_messages(session_id)


@app.get("/api/analytics")
def api_analytics(
    _: Annotated[str, Depends(require_basic_auth)],
) -> dict[str, Any]:
    return get_analytics()


# ----- CTD Code Comparison endpoint -----

_EXTRA_ROWS: list[dict[str, Any]] | None = None


def _load_extra_rows() -> list[dict[str, Any]]:
    """Lazy-load the unified dataset for comparison queries."""
    global _EXTRA_ROWS
    if _EXTRA_ROWS is None:
        from .config import DEFAULT_QNA_PATH
        import re as _re
        if DEFAULT_QNA_PATH.exists():
            with open(DEFAULT_QNA_PATH, encoding="utf-8") as f:
                _EXTRA_ROWS = json.load(f)
        else:
            _EXTRA_ROWS = []
    return _EXTRA_ROWS


def _code_matches(codes_field: str | list, target_code: str) -> bool:
    """Check if codes field (string or list) contains the target parent code."""
    import re as _re
    
    # Handle list format
    if isinstance(codes_field, list):
        codes_to_check = codes_field
    # Handle string format
    elif isinstance(codes_field, str) and codes_field:
        codes_to_check = _re.split(r"[;,]+", codes_field)
    else:
        return False
    
    for raw in codes_to_check:
        raw = str(raw).strip()
        m = _re.match(r"^([SPARsp])\.(\d+(?:\.\d+)?)", raw)
        if m:
            section = f"{m.group(1).upper()}.{m.group(2)}"
            if section == target_code:
                return True
    return False


@app.get("/api/compare")
def api_compare(
    code: str,
    product_a: str,
    product_b: str,
    _: Annotated[str, Depends(require_basic_auth)],
) -> dict[str, Any]:
    """Return Q&A pairs for two products filtered by CTD code, for red-line comparison."""
    rows = _load_extra_rows()
    result_a: list[dict[str, str]] = []
    result_b: list[dict[str, str]] = []

    for r in rows:
        codes_field = r.get("codes", "")
        if not _code_matches(codes_field, code):
            continue
        prod = (r.get("doc_product") or "").strip()
        q = (r.get("Question/Consideration") or "").strip()
        a = (r.get("Answer/Response") or "").strip()
        if not q:
            continue
        entry = {"question": q, "answer": a}
        if prod == product_a:
            result_a.append(entry)
        elif prod == product_b:
            result_b.append(entry)

    return {
        "code": code,
        "product_a": {"name": product_a, "questions": result_a},
        "product_b": {"name": product_b, "questions": result_b},
    }


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
