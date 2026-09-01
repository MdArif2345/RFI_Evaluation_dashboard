"""In-memory index over the Q&A corpus for exact, non-semantic lookups.

Vector search is the wrong tool for two jobs: counting matches across the whole
corpus, and finding a literal identifier like S.4.1. This module handles both by
scanning the 2,048 pairs directly, which takes milliseconds.

Chroma's `where_document={"$contains": ...}` is deliberately not used here: plain
containment matches S.4.10 when asked for S.4.1 (77 hits instead of the correct
75), so matching is boundary-aware regex instead.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from .config import DASHBOARD_DIR, get_settings
from .rag import load_qna_pairs

# Same token shapes the dashboard highlights in index.html.
CODE_TOKEN = re.compile(
    r"\b(?:[A-Z]\.\d+(?:\.\d+)*|\d+\.\d+\.[A-Z]\.\d+(?:\.\d+)*"
    r"|STED\.[\d.]+|M3\.[\d.]+|MD\.[\d.]+)\b"
)

MODULE_PREFIX = re.compile(r"^\d+\.\d+\.(?=[A-Z])")

INSIGHTS_PATH = DASHBOARD_DIR / "qna_insights.json"


def rollup_code(code: str) -> str:
    """Normalise 3.2.P.8.3 and P.8.3.01 onto the same key, P.8.3.

    The corpus mixes full CTD notation with the short form, so without stripping
    the module prefix the same section is counted twice.
    """
    code = MODULE_PREFIX.sub("", code)
    parts = code.split(".")
    return ".".join(parts[:3]) if len(parts) > 3 else code


def extract_codes(text: str) -> set[str]:
    return {rollup_code(c) for c in CODE_TOKEN.findall(text)}


def code_pattern(code: str) -> re.Pattern[str]:
    """Match a CTD code in either notation without bleeding into sibling codes.

    S.4.1 must not match S.4.10, and must still match 3.2.S.4.1 and S.4.1.01.
    """
    bare = MODULE_PREFIX.sub("", code.strip()).strip(".")
    return re.compile(
        r"(?<![\w.])(?:\d+\.\d+\.)?" + re.escape(bare) + r"(?![\d])",
        re.IGNORECASE,
    )


class Corpus:
    def __init__(self, pairs: list[dict[str, str]], theme_by_id: dict[str, int],
                 theme_names: dict[int, str]) -> None:
        self.pairs = pairs
        self.theme_by_id = theme_by_id
        self.theme_names = theme_names

    def resolve_theme(self, name: str) -> int | None:
        """Accept a theme name loosely; the model may paraphrase or change case."""
        target = (name or "").strip().lower()
        if not target:
            return None
        for theme_id, label in self.theme_names.items():
            if label.lower() == target:
                return theme_id
        for theme_id, label in self.theme_names.items():
            if target in label.lower() or label.lower() in target:
                return theme_id
        return None

    def find(self, code: str | None = None, theme: str | None = None) -> list[dict[str, Any]]:
        matches = self.pairs

        if code:
            pattern = code_pattern(code)
            matches = [p for p in matches if pattern.search(p["document"])]

        if theme:
            theme_id = self.resolve_theme(theme)
            if theme_id is None:
                return []
            matches = [p for p in matches if self.theme_by_id.get(p["id"]) == theme_id]

        return matches

    def summarise(
        self, code: str | None = None, theme: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        matches = self.find(code=code, theme=theme)
        limit = max(1, min(int(limit or 20), 50))

        # Longest answers first: those carry the most detail for summarising.
        ranked = sorted(matches, key=lambda p: len(p["answer"]), reverse=True)

        return {
            "total_matches": len(matches),
            "returned": min(limit, len(matches)),
            "filter": {"code": code, "theme": theme},
            "questions": [
                {
                    "id": p["id"],
                    "question": p["question"][:400],
                    "answer_preview": p["answer"][:400],
                }
                for p in ranked[:limit]
            ],
        }

    def available_themes(self) -> list[str]:
        return [self.theme_names[k] for k in sorted(self.theme_names)]


@lru_cache
def get_corpus() -> Corpus:
    """Cached: Settings is unhashable, so this deliberately takes no arguments."""
    settings = get_settings()
    pairs = load_qna_pairs(settings.qna_path)

    theme_by_id: dict[str, int] = {}
    theme_names: dict[int, str] = {}
    if INSIGHTS_PATH.exists():
        insights = json.loads(INSIGHTS_PATH.read_text(encoding="utf-8"))
        theme_by_id = insights.get("theme_by_id", {}) or {}
        for theme in insights.get("themes", []):
            theme_names[int(theme["id"])] = theme["name"]

    return Corpus(pairs, theme_by_id, theme_names)
