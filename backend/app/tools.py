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
                            "description": "How many pairs to return (1-20). Default 8.",
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
                    "Exact filter over the whole corpus by CTD code and/or theme. "
                    "Returns total_matches plus sample questions. This is the only "
                    "way to count or enumerate; semantic search cannot count."
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
                        "limit": {
                            "type": "integer",
                            "description": f"Sample questions to return (1-{MAX_LIST_LIMIT}). Default 20.",
                        },
                    },
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
            "similarity": h["similarity"],
        }
        for h in hits
    ]


def _search_qna(args: dict[str, Any], settings: Settings) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}, []

    top_k = max(1, min(int(args.get("top_k") or 8), 20))
    hits = retrieve(query, settings, top_k=top_k)

    result = {
        "query": query,
        "returned": len(hits),
        "results": [
            {"id": h["id"], "question": h["question"], "answer": h["answer_preview"]}
            for h in hits
        ],
    }
    return result, _sources_from_hits(hits)


def _list_questions(args: dict[str, Any], _settings: Settings) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    code = (args.get("code") or "").strip() or None
    theme = (args.get("theme") or "").strip() or None
    if not code and not theme:
        return {"error": "provide code, theme, or both"}, []

    corpus = get_corpus()
    if theme and corpus.resolve_theme(theme) is None:
        return {
            "error": f"unknown theme '{theme}'",
            "available_themes": corpus.available_themes(),
        }, []

    limit = int(args.get("limit") or 20)
    summary = corpus.summarise(code=code, theme=theme, limit=limit)

    sources = [
        {
            "id": q["id"],
            "question": q["question"],
            "answer_preview": q["answer_preview"],
            # Exact filter, not a ranked match: no meaningful similarity score.
            "similarity": 1.0,
        }
        for q in summary["questions"]
    ]
    return summary, sources


DISPATCH = {
    "search_qna": _search_qna,
    "list_questions": _list_questions,
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
        return f"Searching the corpus for “{query[:60]}”" if query else "Searching the corpus"

    if name == "list_questions":
        code = (args.get("code") or "").strip()
        theme = (args.get("theme") or "").strip()
        if code and theme:
            return f"Counting {code} questions under {theme}"
        if code:
            return f"Counting matches for {code}"
        if theme:
            return f"Listing questions under {theme}"
        return "Filtering the corpus"

    return f"Running {name}"
