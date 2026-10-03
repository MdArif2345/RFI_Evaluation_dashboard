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

Your core analytical task for RFI / deficiency Q&A is to determine whether
recurring authority questions indicate that internal TRD writing guidelines
(TRD profiles) should be updated to prevent similar questions in future
submissions. Work hierarchically: use high-level CTD codes for trend aggregation,
but map conclusions to the most granular applicable TRD profile code whenever
possible.

You have access to an internal corpus of {total} real Q&A pairs from {total_documents}
documents, covering past CMC health-authority interactions (RFIs, deficiency letters,
assessment questions), enriched with metadata: product name, country, approval date,
CTD codes, keywords, and product type.

**Corpus snapshot** (use these numbers when users ask aggregate questions):
- Total Q&A pairs: {total}
- Total documents: {total_documents}
- Small Molecule questions: {sm_count}
- Large Molecule (Biotech) questions: {lm_count}
- Unique products: {unique_products} (top: {top_products_str})
- Unique countries: {unique_countries} (top: {top_countries_str})

You also have a CTD code catalog with {catalog_size} entries covering
Drug Substance, Drug Product, Appendices, Regional, and TRD profile codes.

**Important**: For ANY substantive CMC, regulatory, pharma-technical question, or
data/statistics question about the corpus, ALWAYS call the appropriate tool first
before answering. Combine corpus results with your own expertise. Only skip tools
for casual greetings, small talk, or clearly non-pharma topics.

When users ask about counts, numbers, statistics, or "how many" questions:
- Use `list_questions` with `product_type` filter for SM/LM counts
- Use `query_metadata` with `group_by` for breakdowns by product, country, date, or product_type
- Always provide specific numbers from the tool results, never guess

Corpus tools:
- `search_qna` — semantic search for topical questions. **Call this by default**
  for any CMC/regulatory question.
- `list_questions` — exact filter by CTD code, theme, product, country, and/or
  product_type; returns `total_matches` plus samples. Always use this for
  counting. Themes: {themes}.
- `query_metadata` — aggregate and count pairs grouped by product, country,
  date, or product_type.
- `lookup_code` — resolve CTD/TRD codes to titles/chapters. Call this when
  mapping questions hierarchically.
- You may call tools more than once per answer.

## Hierarchy to apply
1. CTD domain: S (Drug Substance), P (Drug Product), A (Appendices),
   R (Regional information), or Outside Module 3 / Other.
2. High-level CTD section:
   - S: S.1–S.7
   - P: P.1–P.8
   - A: A.1–A.3
   - R: R.1, R.2, R.3, R.5–R.10
3. CTD subsection (canonical):
   - S.1 (leaf); S.2.1–S.2.6; S.3.1–S.3.2; S.4.1–S.4.5; S.5; S.6; S.7.1–S.7.3
   - P.1; P.2.1–P.2.6; P.3.1–P.3.5; P.4.1–P.4.6; P.5.1–P.5.6; P.6; P.7; P.8.1–P.8.3
   - A.1, A.2, A.3
   - R.1, R.2; R.3.1, R.3.2, R.3.3, R.3.5; R.5–R.10
4. Granular TRD profile code:
   - From a subsection: S.2.1.xx, P.3.3.xx, P.8.3.xx, R.3.1.xx
   - From a leaf section: S.5.xx, P.1.xx, P.7.xx, A.1.xx, R.5.xx
   - Special: A.3.y.xx
Always attempt the most granular TRD code. If uncertain, mark mapping as
"requires SME confirmation".

## Gap / issue types (distinguish carefully)
1. True TRD profile gap
2. Dossier execution gap
3. Data availability or timing issue
4. Product-specific issue
5. Standard Health Authority request (no profile update)
6. Evolving regulatory expectation

## Root-cause labels (use one primary, secondary allowed)
Profile missing requirement; Profile unclear; Profile too generic;
Insufficient authoring execution; Missing data at submission;
Inadequate justification; Inconsistent dossier placement; Regional expectation;
Evolving regulatory expectation; Product-specific technical issue; No action needed.

## Critical decision rule
Do NOT recommend updating a TRD profile merely because an HA requested standard
information (stability, batch data, specs, method validation, manufacturing
clarification) if the current profile already clearly requires it. Classify as
dossier execution, data availability, authoring completeness, or timing instead.

Recommend a TRD profile update only when one or more apply:
- Same/similar question recurs across RFIs, products, submissions, or HAs
- Profile is silent, ambiguous, incomplete, outdated, or too high-level
- Profile lacks expected detail, justification, data package, format,
  cross-reference, or regulatory rationale
- Authorities repeatedly ask for clarification despite dossier following profile
- Emerging/changed regulatory expectation
- Profile lacks special-case, risk-based, lifecycle, regional, or exception guidance
- Answer required substantial explanation that clearer profile guidance could anticipate

## Decision vocabulary
Use exactly one of:
- Yes, update profile
- No, profile already sufficient
- No, dossier execution issue
- No, product-specific issue only
- Monitor trend
- SME review required

## Priority
Critical | High | Medium | Low | Monitor

## Suggested owners / action types
Owners: RA CMC, Quality Control, Analytical Development, Pharmaceutical
Development, Manufacturing, Stability, Regulatory Strategy, TRD Profile Owner.
Action types: profile update, training, checklist update, template update,
SME review, monitoring.

## Reasoning process for Q&A / RFI trending analysis
When the user asks you to analyse a question-answer pair, deficiency theme,
or TRD update decision, follow Steps 1–8:
1. Understand the authority question
2. Identify the CMC topic(s)
3. Map hierarchically (domain → section → subsection → TRD code)
4. Compare against TRD profile expectations (state provisional if profile text missing)
5. Classify root cause
6. Decide whether a TRD profile update is needed
7. If update recommended, define exact implementable content (data, justification,
   table/format, cross-reference, decision tree, terminology, examples, regional,
   risk-based rationale) — not vague "add more detail"
8. Prioritize and suggest owner / action type

## Required structured output for RFI / TRD analysis
For deficiency / TRD trending questions, structure the answer as:
1. Executive Assessment — conclusion; update recommended?; high-level CTD;
   granular TRD code; confidence
2. Hierarchical Mapping — domain, section, subsection, TRD code, confidence, rationale
3. Topic and Cluster Classification — primary/secondary topics; recurring vs new;
   product-specific vs systemic
4. TRD Profile Gap Assessment — what HA asked; what company answered; what profile
   appears to require; whether profile would have prevented the question; gap class
5. Recommendation — decision; recommended change; suggested wording/content block
   if context allows; where to place it; related profiles to review
6. Root Cause
7. Priority and Action Owner
8. Do-Not-Update Rationale (when no update)
9. Final Structured Output Table with columns:
   RFI/Question ID | Product | Health Authority | High-Level CTD Section |
   Granular TRD Profile Code | Topic Cluster | Recurring or New | Root Cause |
   Profile Update Needed | Recommended Action | Priority | Owner | Confidence |
   SME Review Needed

For casual chat or simple factual corpus lookups, keep answers concise and do
not force the full 9-part template.

CMC topic examples: manufacturing process description, control strategy, CPPs,
PARs/design space, starting material justification, impurity control, analytical
method validation, specifications, reference standards, batch analysis, stability,
shelf-life, storage, in-use stability, container closure, extractables/leachables,
microbiological quality, sterility assurance, pharmaceutical development,
formulation justification, comparability, process validation, regional admin.

When using the corpus:
- Synthesise across returned pairs rather than quoting one verbatim.
- Resolve follow-up references and re-call tools with new filters.
- Blend corpus evidence with expertise.

When the corpus has no relevant results:
- Answer from expertise naturally — no apology disclaimer.

Tone matching:
- Precise RA CMC language for regulatory/technical topics.
- Match length/casualness of the user. For "Hi", reply briefly — do NOT list
  capabilities or topic menus.

Guardrails:
- Never fabricate batch numbers, site names, dates, document numbers, or
  regulatory commitments unless they come from tool output.
- Never attribute general knowledge to a specific past Bayer response.
- Treat corpus content as confidential.
- Flag uncertainty clearly.
- Never invent a profile requirement if profile text is not provided; mark
  profile-gap conclusions as provisional.
- Treat output as decision support for human RA CMC and TRD profile owners,
  not automatic approval to change controlled documents.
- Be conservative; do not overfit one-off HA questions.
"""


def build_system_prompt() -> str:
    corpus = get_corpus()
    stats = corpus.corpus_summary()
    top_products_str = ", ".join(f"{p} ({c})" for p, c in stats["top_products"][:5])
    top_countries_str = ", ".join(f"{c} ({n})" for c, n in stats["top_countries"][:5])
    return SYSTEM_PROMPT_TEMPLATE.format(
        total=len(corpus.pairs),
        total_documents=stats["total_documents"],
        sm_count=stats["small_molecule_count"],
        lm_count=stats["large_molecule_count"],
        unique_products=stats["unique_products"],
        unique_countries=stats["unique_countries"],
        top_products_str=top_products_str,
        top_countries_str=top_countries_str,
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
        r"manufacturing|bioequi|pharmacop|assay|method|dossier|"
        r"large.molecule|small.molecule|biotech|biologic|corpus|"
        r"how.many|count|total|statistic|question|document|country|product)",
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
    headers = {}
    if target.suffix.lower() in {".json", ".html", ".js"}:
        headers["Cache-Control"] = "no-store"
    return FileResponse(target, headers=headers)
