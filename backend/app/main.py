from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Iterator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .auth import require_basic_auth
from .config import Settings, DASHBOARD_DIR, get_settings
from .rag import chat_client, ingest, retrieve

SYSTEM_PROMPT = """You are a CMC regulatory Q&A assistant for an internal dashboard.

Ground every answer in the provided context, which contains real question/answer
pairs from past CMC health-authority responses. The context has already been
selected as the closest matches to the user's question.

Rules:
- Use the context as your source of truth; summarize and synthesize across sources.
- Only state that no matching CMC response exists if the context genuinely covers
  unrelated topics.
- Never invent batch numbers, sites, dates, or regulatory commitments.
- Mention which source question(s) you relied on when helpful.
- Treat all content as confidential CMC regulatory information.
"""

NO_MATCH_MESSAGE = (
    "I don't have a matching CMC response in the indexed Q&A pairs for that question. "
    "Try rephrasing or a more specific CMC/regulatory topic."
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


def _relevant_hits(message: str, settings: Settings) -> list[dict[str, Any]]:
    hits = retrieve(message, settings)
    return [h for h in hits if h["similarity"] >= settings.min_relevance]


def _to_sources(hits: list[dict[str, Any]]) -> list[SourceOut]:
    return [
        SourceOut(
            id=h["id"],
            question=h["question"],
            answer_preview=h["answer_preview"],
            similarity=h["similarity"],
        )
        for h in hits
    ]


def _build_messages(message: str, hits: list[dict[str, Any]]) -> list[dict[str, str]]:
    # Similarity scores stay out of the prompt: the model reads low decimals as a
    # signal the context is weak and refuses to answer.
    context = "\n\n---\n\n".join(
        f"[Source {i}]\n{h['document']}" for i, h in enumerate(hits, start=1)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Context from CMC Q&A index:\n\n{context}\n\nUser question:\n{message}",
        },
    ]


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
    try:
        relevant = _relevant_hits(message, settings)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Retrieval failed (index missing? run scripts/ingest.py): {exc}",
        ) from exc

    if not relevant:
        return ChatResponse(answer=NO_MATCH_MESSAGE, sources=[], used_retrieval=False)

    sources = _to_sources(relevant)

    try:
        client = chat_client(settings)
        completion = client.chat.completions.create(
            model=settings.chat_model(),
            temperature=0.2,
            messages=_build_messages(message, relevant),
        )
        answer = (completion.choices[0].message.content or "").strip()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"{settings.llm_provider.upper()} chat failed: {exc}",
        ) from exc

    return ChatResponse(answer=answer, sources=sources, used_retrieval=True)


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
            yield emit({"type": "stage", "stage": "retrieving"})
            relevant = _relevant_hits(message, settings)

            if not relevant:
                yield emit({"type": "sources", "sources": []})
                yield emit({"type": "delta", "text": NO_MATCH_MESSAGE})
                yield emit({"type": "done", "used_retrieval": False})
                return

            yield emit({"type": "stage", "stage": "retrieved", "count": len(relevant)})
            yield emit(
                {
                    "type": "sources",
                    "sources": [s.model_dump() for s in _to_sources(relevant)],
                }
            )
            yield emit({"type": "stage", "stage": "generating"})

            client = chat_client(settings)
            stream = client.chat.completions.create(
                model=settings.chat_model(),
                temperature=0.2,
                messages=_build_messages(message, relevant),
                stream=True,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                piece = chunk.choices[0].delta.content
                if piece:
                    yield emit({"type": "delta", "text": piece})

            yield emit({"type": "done", "used_retrieval": True})
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
