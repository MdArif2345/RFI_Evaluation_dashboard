"""Tools the chat model can call, plus their OpenAI function schemas.

Each tool returns a JSON-serialisable dict for the model and a list of source
records for the UI, so the sources panel stays populated no matter which tool
produced the evidence.
"""

from __future__ import annotations

import json
from typing import Any

from .config import Settings
from .corpus import get_corpus
from .rag import retrieve

MAX_LIST_LIMIT = 50


def tool_schemas() -> list[dict[str, Any]]:
    themes = get_corpus().available_themes()
    return [
        {
            "type": "function",
            "function": {
                "name": "search_qna",
                "description": (
                    "Semantic search over past CMC health-authority Q&A pairs. "
                    "Use for topical questions where wording varies. Returns the "
                    "closest matching question/answer pairs."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural-language search query.",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "How many pairs to return (1-20). Default 5.",
                        },
                        "product": {
                            "type": "string",
                            "description": "Optional: restrict results to this product (e.g. Aflibercept, Gadoquatrane).",
                        },
                        "country": {
                            "type": "string",
                            "description": "Optional: restrict results to this country (e.g. India, Japan).",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_questions",
                "description": (
                    "Exact filter over the whole corpus by CTD code, theme, product, "
                    "and/or country. Returns total_matches plus sample questions. "
                    "This is the only way to count or enumerate; semantic search cannot count."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": (
                                "CTD section code, e.g. S.4.1 or P.8.3. Either "
                                "notation works; 3.2.S.4.1 and S.4.1 are the same."
                            ),
                        },
                        "theme": {
                            "type": "string",
                            "description": "One of: " + "; ".join(themes),
                        },
                        "product": {
                            "type": "string",
                            "description": "Filter by product/substance name (e.g. Gadoquatrane, Aflibercept).",
                        },
                        "country": {
                            "type": "string",
                            "description": "Filter by submission country (e.g. India, Germany, USA).",
                        },
                        "limit": {
                            "type": "integer",
                            "description": f"Sample questions to return (1-{MAX_LIST_LIMIT}). Default 20.",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_metadata",
                "description": (
                    "Aggregate and count Q&A pairs grouped by product, country, or date. "
                    "Use for questions like 'which product has the most questions', "
                    "'how many questions from India', or 'which month had the most submissions'. "
                    "Optionally filter by code, theme, product, or country before aggregating."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "group_by": {
                            "type": "string",
                            "enum": ["product", "country", "date"],
                            "description": "Dimension to group and count by.",
                        },
                        "code": {
                            "type": "string",
                            "description": "Optional CTD code filter before aggregation.",
                        },
                        "theme": {
                            "type": "string",
                            "description": "Optional theme filter before aggregation.",
                        },
                        "product": {
                            "type": "string",
                            "description": "Optional product filter before aggregation.",
                        },
                        "country": {
                            "type": "string",
                            "description": "Optional country filter before aggregation.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max groups to return (1-50). Default 15.",
                        },
                    },
                    "required": ["group_by"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lookup_code",
                "description": (
                    "Look up a CTD code in the code catalog to find its title, chapter, "
                    "and subchapter. Use when the user asks 'what does P.8.3 cover?' or "
                    "'what is S.4.1?'. The catalog has 706 entries covering Drug Substance, "
                    "Drug Product, Appendices, STED, Module 3, and Medical Devices."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "The CTD code to look up (e.g. P.8.3, S.4.1, A.1.01).",
                        },
                    },
                    "required": ["code"],
                },
            },
        },
    ]


def _sources_from_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": h["id"],
            "question": h["question"],
            "answer_preview": h["answer_preview"],
            "doc_id": h.get("doc_id", ""),
            "similarity": h["similarity"],
        }
        for h in hits
    ]


def _search_qna(args: dict[str, Any], settings: Settings) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}, []

    top_k = max(1, min(int(args.get("top_k") or 5), 20))

    # Build Chroma where clause from optional metadata filters.
    conditions: list[dict[str, str]] = []
    product = (args.get("product") or "").strip()
    country = (args.get("country") or "").strip()
    if product:
        conditions.append({"product": product})
    if country:
        conditions.append({"country": country})

    where: dict[str, Any] | None = None
    if len(conditions) == 1:
        where = conditions[0]
    elif len(conditions) > 1:
        where = {"$and": conditions}

    hits = retrieve(query, settings, top_k=top_k, where=where)

    result = {
        "query": query,
        "returned": len(hits),
        "filters": {"product": product or None, "country": country or None},
        "results": [
            {"id": h["id"], "question": h["question"], "answer": h["answer_preview"]}
            for h in hits
        ],
    }
    return result, _sources_from_hits(hits)


def _list_questions(args: dict[str, Any], _settings: Settings) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    code = (args.get("code") or "").strip() or None
    theme = (args.get("theme") or "").strip() or None
    product = (args.get("product") or "").strip() or None
    country = (args.get("country") or "").strip() or None
    if not code and not theme and not product and not country:
        return {"error": "provide at least one filter: code, theme, product, or country"}, []

    corpus = get_corpus()
    if theme and corpus.resolve_theme(theme) is None:
        return {
            "error": f"unknown theme '{theme}'",
            "available_themes": corpus.available_themes(),
        }, []

    limit = int(args.get("limit") or 20)
    summary = corpus.summarise(code=code, theme=theme, product=product, country=country, limit=limit)

    return summary, []


def _query_metadata(args: dict[str, Any], _settings: Settings) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    group_by = (args.get("group_by") or "").strip().lower()
    if group_by not in ("product", "country", "date"):
        return {"error": "group_by must be one of: product, country, date"}, []

    code = (args.get("code") or "").strip() or None
    theme = (args.get("theme") or "").strip() or None
    product = (args.get("product") or "").strip() or None
    country = (args.get("country") or "").strip() or None
    limit = int(args.get("limit") or 15)

    corpus = get_corpus()
    result = corpus.aggregate(
        group_by=group_by,
        code=code,
        theme=theme,
        product=product,
        country=country,
        limit=limit,
    )
    return result, []


def _lookup_code(args: dict[str, Any], _settings: Settings) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    code = (args.get("code") or "").strip()
    if not code:
        return {"error": "code is required"}, []

    corpus = get_corpus()
    entry = corpus.lookup_code(code)
    if entry is None:
        return {"error": f"code '{code}' not found in the catalog"}, []

    return entry, []


DISPATCH = {
    "search_qna": _search_qna,
    "list_questions": _list_questions,
    "query_metadata": _query_metadata,
    "lookup_code": _lookup_code,
}


def run_tool(name: str, raw_args: str, settings: Settings) -> tuple[str, list[dict[str, Any]]]:
    """Execute a tool call; returns (json string for the model, source records)."""
    handler = DISPATCH.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool '{name}'"}), []

    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        return json.dumps({"error": "arguments were not valid JSON"}), []

    try:
        result, sources = handler(args, settings)
    except Exception as exc:
        return json.dumps({"error": str(exc)[:300]}), []

    return json.dumps(result, ensure_ascii=False), sources


def describe_call(name: str, raw_args: str) -> str:
    """Short human phrase for the UI's thinking indicator."""
    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        args = {}

    if name == "search_qna":
        query = str(args.get("query") or "").strip()
        parts = []
        if query:
            parts.append(f"\u201c{query[:60]}\u201d")
        product = (args.get("product") or "").strip()
        country = (args.get("country") or "").strip()
        if product:
            parts.append(product)
        if country:
            parts.append(country)
        return f"Searching the corpus for {', '.join(parts)}" if parts else "Searching the corpus"

    if name == "list_questions":
        parts = []
        code = (args.get("code") or "").strip()
        theme = (args.get("theme") or "").strip()
        product = (args.get("product") or "").strip()
        country = (args.get("country") or "").strip()
        if code:
            parts.append(code)
        if theme:
            parts.append(theme)
        if product:
            parts.append(product)
        if country:
            parts.append(country)
        return f"Filtering corpus by {', '.join(parts)}" if parts else "Filtering the corpus"

    if name == "query_metadata":
        group_by = (args.get("group_by") or "").strip()
        return f"Counting by {group_by}" if group_by else "Aggregating metadata"

    if name == "lookup_code":
        code = (args.get("code") or "").strip()
        return f"Looking up {code}" if code else "Looking up a code"

    return f"Running {name}"
