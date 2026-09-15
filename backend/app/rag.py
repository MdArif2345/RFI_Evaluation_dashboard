"""Load, clean, and index CMC Q&A pairs into Chroma.

Embeddings default to a local on-device model so no corpus text leaves the
machine during indexing. Chat generation goes to myGenAssist (or OpenAI).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils import embedding_functions
from openai import OpenAI

from .config import EXTRA_QNA_PATH, FALLBACK_QNA_PATH, Settings, get_settings

_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS.sub(" ", (text or "").strip())


def _is_bad(value: str) -> bool:
    v = value.strip().lower()
    return not v or v in {"n.a.", "n/a", "na", "none", "-"}


def _parse_codes_field(value: Any) -> list[str]:
    """Extract CTD codes from the JSONL `codes` column (string or list)."""
    if isinstance(value, list):
        return [str(c).strip() for c in value if str(c).strip()]
    if isinstance(value, str) and value.strip():
        return [c.strip() for c in re.split(r"[;,\n]+", value) if c.strip()]
    return []


def _parse_keywords_field(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(k).strip() for k in value if str(k).strip()]
    if isinstance(value, str) and value.strip():
        return [k.strip() for k in re.split(r"[;,\n]+", value) if k.strip()]
    return []


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse the JSONL enriched file — one JSON object per line.

    Deduplicates by (question + answer) so that the same question answered
    differently for different products/countries is kept, while true
    duplicates (same Q, same A) are dropped.
    """
    cleaned: list[dict[str, Any]] = []
    seen_pairs: set[str] = set()
    seen_questions: set[str] = set()
    idx = 0

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue

        question = _normalize(str(
            row.get("Question/Consideration", "")
            or row.get("question", "")
            or row.get("Question", "")
        ))
        answer = _normalize(str(
            row.get("Answer/Response", "")
            or row.get("answer", "")
            or row.get("Answer", "")
        ))
        if _is_bad(question) or _is_bad(answer):
            continue

        pair_key = (question + "\n" + answer).lower()
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        seen_questions.add(question.lower())

        cleaned.append(
            {
                "id": f"qna-{idx}",
                "question": question,
                "answer": answer,
                "document": f"Question: {question}\n\nAnswer: {answer}",
                "product": _normalize(str(row.get("doc_product", "") or "")),
                "country": _normalize(str(row.get("doc_country", "") or "")),
                "date": _normalize(str(row.get("doc_approve_date", "") or "")),
                "doc_name": _normalize(str(row.get("doc_name", "") or "")),
                "doc_id": str(row.get("doc_id", "") or ""),
                "codes": _parse_codes_field(row.get("codes", "")),
                "keywords": _parse_keywords_field(row.get("keywords", "")),
            }
        )
        idx += 1

    return cleaned, seen_questions


def _load_flat_json(path: Path) -> list[dict[str, Any]]:
    """Parse the original flat JSON array of {Question, Answer}."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Expected a JSON array in {path}")

    cleaned: list[dict[str, Any]] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            continue
        question = _normalize(str(row.get("Question", "")))
        answer = _normalize(str(row.get("Answer", "")))
        if _is_bad(question) or _is_bad(answer):
            continue
        cleaned.append(
            {
                "id": f"qna-{i}",
                "question": question,
                "answer": answer,
                "document": f"Question: {question}\n\nAnswer: {answer}",
                "product": "",
                "country": "",
                "date": "",
                "doc_name": "",
                "doc_id": "",
                "codes": [],
                "keywords": [],
            }
        )
    return cleaned


def _merge_extra_json(
    extra_path: Path,
    pairs: list[dict[str, Any]],
    seen_questions: set[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Merge Q&A pairs from the extra enriched JSON array file.

    Deduplicates by (question + answer) to avoid true duplicates while
    keeping the same question with different answers.
    """
    raw = json.loads(extra_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return pairs, seen_questions

    seen_pairs = {(p["question"] + "\n" + p["answer"]).lower() for p in pairs}
    next_idx = len(pairs)

    for row in raw:
        if not isinstance(row, dict):
            continue
        question = _normalize(str(row.get("Question/Consideration", "")))
        answer = _normalize(str(row.get("Answer/Response", "")))
        if _is_bad(question) or _is_bad(answer):
            continue

        pair_key = (question + "\n" + answer).lower()
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        seen_questions.add(question.lower())

        pairs.append({
            "id": f"qna-{next_idx}",
            "question": question,
            "answer": answer,
            "document": f"Question: {question}\n\nAnswer: {answer}",
            "product": _normalize(str(row.get("doc_product", "") or "")),
            "country": _normalize(str(row.get("doc_country", "") or "")),
            "date": _normalize(str(row.get("doc_approve_date", "") or "")),
            "doc_name": _normalize(str(row.get("doc_name", "") or "")),
            "doc_id": str(row.get("doc_id", "") or ""),
            "doc_product_type": _normalize(str(row.get("doc_product_type", "") or "")),
            "codes": _parse_codes_field(row.get("codes", "")),
            "keywords": _parse_keywords_field(row.get("keywords", "")),
        })
        next_idx += 1

    return pairs, seen_questions


def load_qna_pairs(path: Path) -> list[dict[str, Any]]:
    """Load Q&A pairs from JSONL (primary) with fallback merge from flat JSON.

    The JSONL is a superset of the flat JSON but carries rich metadata. The
    original QnA_pairs_extracted.json is never modified; any pair whose
    normalised question text is missing from the JSONL is appended with
    empty metadata fields.
    """
    if path.suffix == ".jsonl" or path.name.endswith(".jsonl"):
        pairs, seen = _load_jsonl(path)

        # Merge any pairs from the original flat JSON that the JSONL missed.
        if FALLBACK_QNA_PATH.exists() and FALLBACK_QNA_PATH != path:
            fallback = _load_flat_json(FALLBACK_QNA_PATH)
            next_idx = len(pairs)
            for p in fallback:
                if p["question"].lower() not in seen:
                    p["id"] = f"qna-{next_idx}"
                    pairs.append(p)
                    seen.add(p["question"].lower())
                    next_idx += 1

        # Merge the extra enriched JSON file (separate data source, not
        # concatenated into the JSONL).  Dedup by (question+answer).
        if EXTRA_QNA_PATH.exists() and EXTRA_QNA_PATH != path:
            pairs, seen = _merge_extra_json(EXTRA_QNA_PATH, pairs, seen)

        return pairs

    # Legacy path: plain JSON array (no metadata).
    return _load_flat_json(path)


def _placeholder(value: str) -> bool:
    return not value or value.startswith("REPLACE_")


def get_embedding_function(settings: Settings):
    if settings.embed_provider == "local":
        # all-MiniLM-L6-v2 ONNX, runs on-device; model cached after first use.
        return embedding_functions.DefaultEmbeddingFunction()

    if settings.embed_provider == "openai":
        if _placeholder(settings.openai_api_key):
            raise RuntimeError(
                "EMBED_PROVIDER=openai but OPENAI_API_KEY is missing/placeholder in backend/.env"
            )
        return embedding_functions.OpenAIEmbeddingFunction(
            api_key=settings.openai_api_key,
            model_name=settings.openai_embed_model,
        )

    # embed_provider == "mga"
    if _placeholder(settings.mga_api_key) or not settings.mga_base_url:
        raise RuntimeError(
            "EMBED_PROVIDER=mga requires MGA_API_KEY and MGA_BASE_URL in backend/.env"
        )
    return embedding_functions.OpenAIEmbeddingFunction(
        api_key=settings.mga_api_key,
        model_name=settings.mga_embed_model,
        api_base=settings.mga_base_url,
    )


def get_collection(settings: Settings | None = None):
    settings = settings or get_settings()
    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    return client.get_or_create_collection(
        name=settings.chroma_collection,
        embedding_function=get_embedding_function(settings),
        metadata={"hnsw:space": "cosine"},
    )


def ingest(settings: Settings | None = None, *, reset: bool = True) -> dict[str, Any]:
    settings = settings or get_settings()
    pairs = load_qna_pairs(settings.qna_path)
    if not pairs:
        raise RuntimeError(f"No usable Q&A pairs found in {settings.qna_path}")

    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))

    if reset:
        try:
            client.delete_collection(settings.chroma_collection)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=settings.chroma_collection,
        embedding_function=get_embedding_function(settings),
        metadata={"hnsw:space": "cosine"},
    )

    batch_size = 64
    for start in range(0, len(pairs), batch_size):
        batch = pairs[start : start + batch_size]
        collection.upsert(
            ids=[p["id"] for p in batch],
            documents=[p["document"] for p in batch],
            metadatas=[
                {
                    "question": p["question"][:500],
                    "answer_preview": p["answer"][:500],
                    "product": (p.get("product") or "")[:200],
                    "country": (p.get("country") or "")[:200],
                    "doc_id": (p.get("doc_id") or "")[:50],
                }
                for p in batch
            ],
        )

    return {
        "indexed": len(pairs),
        "collection": settings.chroma_collection,
        "embed_provider": settings.embed_provider,
        "chroma_dir": str(settings.chroma_dir),
        "source": str(settings.qna_path),
    }


def retrieve(
    query: str,
    settings: Settings | None = None,
    *,
    top_k: int | None = None,
    where: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    collection = get_collection(settings)
    k = top_k or settings.retrieve_top_k
    extra: dict[str, Any] = {}
    if where:
        extra["where"] = where
    result = collection.query(query_texts=[query], n_results=k, **extra)

    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    ids = (result.get("ids") or [[]])[0]

    hits: list[dict[str, Any]] = []
    for i, doc in enumerate(docs):
        distance = float(dists[i]) if i < len(dists) else 1.0
        # Chroma cosine distance: lower is better; convert to rough similarity.
        similarity = max(0.0, 1.0 - distance)
        meta = metas[i] if i < len(metas) else {}
        hits.append(
            {
                "id": ids[i] if i < len(ids) else f"hit-{i}",
                "document": doc,
                "question": (meta or {}).get("question", ""),
                "answer_preview": (meta or {}).get("answer_preview", ""),
                "doc_id": (meta or {}).get("doc_id", ""),
                "similarity": round(similarity, 4),
                "distance": round(distance, 4),
            }
        )
    return hits


def chat_client(settings: Settings | None = None) -> OpenAI:
    """OpenAI-compatible client pointed at myGenAssist (or public OpenAI)."""
    settings = settings or get_settings()

    if settings.llm_provider == "openai":
        if _placeholder(settings.openai_api_key):
            raise RuntimeError(
                "LLM_PROVIDER=openai but OPENAI_API_KEY is missing/placeholder in backend/.env"
            )
        return OpenAI(api_key=settings.openai_api_key)

    if _placeholder(settings.mga_api_key):
        raise RuntimeError(
            "MGA_API_KEY is missing or still a placeholder in backend/.env"
        )
    if not settings.mga_base_url:
        raise RuntimeError(
            "MGA_BASE_URL is not set in backend/.env "
            "(ask your myGenAssist admin for the API endpoint)"
        )

    default_headers: dict[str, str] = {}
    default_query: dict[str, str] = {}

    # Some gateways expect the key in a custom header rather than Bearer auth.
    if settings.mga_auth_header.lower() != "authorization":
        default_headers[settings.mga_auth_header] = settings.mga_api_key
    if settings.mga_api_version:
        default_query["api-version"] = settings.mga_api_version

    return OpenAI(
        api_key=settings.mga_api_key,
        base_url=settings.mga_base_url,
        default_headers=default_headers or None,
        default_query=default_query or None,
    )


# Backwards-compatible alias
openai_client = chat_client
