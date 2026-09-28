"""In-memory index over the Q&A corpus for exact, non-semantic lookups.

Vector search is the wrong tool for two jobs: counting matches across the whole
corpus, and finding a literal identifier like S.4.1. This module handles both by
scanning the pairs directly, which takes milliseconds.

This module also loads the CTD code catalog (706 entries) so the model can
resolve any code to its title, chapter, and subchapter.
"""

from __future__ import annotations

import collections
import json
import re
from functools import lru_cache
from typing import Any

from .config import CODE_CATALOG_PATH, DASHBOARD_DIR, get_settings
from .rag import load_qna_pairs

# Same token shapes the dashboard highlights in index.html.
CODE_TOKEN = re.compile(
    r"\b(?:[A-Z]\.\d+(?:\.\d+)*|\d+\.\d+\.[A-Z]\.\d+(?:\.\d+)*"
    r"|STED\.[\d.]+|M3\.[\d.]+|MD\.[\d.]+)\b"
)

MODULE_PREFIX = re.compile(r"^\d+\.\d+\.(?=[A-Z])")

INSIGHTS_PATH = DASHBOARD_DIR / "qna_insights.json"


def rollup_code(code: str) -> str:
    """Normalise 3.2.P.8.3 and P.8.3.01 onto the same key, P.8.3."""
    code = MODULE_PREFIX.sub("", code)
    parts = code.split(".")
    return ".".join(parts[:3]) if len(parts) > 3 else code


def extract_codes(text: str) -> set[str]:
    return {rollup_code(c) for c in CODE_TOKEN.findall(text)}


def code_pattern(code: str) -> re.Pattern[str]:
    """Match a CTD code without bleeding into sibling codes."""
    bare = MODULE_PREFIX.sub("", code.strip()).strip(".")
    return re.compile(
        r"(?<![\w.])(?:\d+\.\d+\.)?" + re.escape(bare) + r"(?![\d])",
        re.IGNORECASE,
    )


def _load_code_catalog() -> dict[str, dict[str, str]]:
    """Load code_catalog.json into a dict keyed by code."""
    if not CODE_CATALOG_PATH.exists():
        return {}
    try:
        raw = json.loads(CODE_CATALOG_PATH.read_text(encoding="utf-8"))
        entries = raw.get("body", raw) if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            return {}
        catalog: dict[str, dict[str, str]] = {}
        for entry in entries:
            code = (entry.get("code") or "").strip()
            if not code:
                continue
            catalog[code] = {
                "code": code,
                "title": entry.get("title", ""),
                "chapter": entry.get("chapter", ""),
                "chapter_title": entry.get("chapter_title", ""),
                "subchapter": entry.get("subchapter", ""),
                "corresponding_t_code": entry.get("corresponding_t_code", ""),
            }
        return catalog
    except Exception:
        return {}


class Corpus:
    def __init__(
        self,
        pairs: list[dict[str, Any]],
        theme_by_id: dict[str, int],
        theme_names: dict[int, str],
        code_catalog: dict[str, dict[str, str]],
    ) -> None:
        self.pairs = pairs
        self.theme_by_id = theme_by_id
        self.theme_names = theme_names
        self.code_catalog = code_catalog

    # ------------------------------------------------------------------
    # Theme helpers
    # ------------------------------------------------------------------
    def resolve_theme(self, name: str) -> int | None:
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

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------
    def find(
        self,
        code: str | None = None,
        theme: str | None = None,
        product: str | None = None,
        country: str | None = None,
        product_type: str | None = None,
    ) -> list[dict[str, Any]]:
        matches = self.pairs

        if code:
            bare = MODULE_PREFIX.sub("", code.strip()).strip(".")
            def _code_match(p: dict[str, Any]) -> bool:
                codes_field = p.get("codes", [])
                if isinstance(codes_field, list):
                    for c in codes_field:
                        c_str = str(c).strip()
                        m = re.match(r"^([SPARsp])\.(\d+(?:\.\d+)?)", c_str)
                        if m and f"{m.group(1).upper()}.{m.group(2)}" == bare:
                            return True
                elif isinstance(codes_field, str) and codes_field:
                    for c in re.split(r"[;,]+", codes_field):
                        c_str = c.strip()
                        m = re.match(r"^([SPARsp])\.(\d+(?:\.\d+)?)", c_str)
                        if m and f"{m.group(1).upper()}.{m.group(2)}" == bare:
                            return True
                return False
            matches = [p for p in matches if _code_match(p)]

        if theme:
            theme_id = self.resolve_theme(theme)
            if theme_id is None:
                return []
            matches = [p for p in matches if self.theme_by_id.get(p["id"]) == theme_id]

        if product:
            prod_lower = product.strip().lower()
            matches = [p for p in matches if (p.get("product") or "").lower() == prod_lower]

        if country:
            country_lower = country.strip().lower()
            matches = [p for p in matches if (p.get("country") or "").lower() == country_lower]

        if product_type:
            pt_lower = product_type.strip().lower()
            matches = [
                p for p in matches
                if pt_lower in (p.get("doc_product_type") or "").lower()
            ]

        return matches

    def summarise(
        self,
        code: str | None = None,
        theme: str | None = None,
        product: str | None = None,
        country: str | None = None,
        product_type: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        matches = self.find(code=code, theme=theme, product=product, country=country, product_type=product_type)
        limit = max(1, min(int(limit or 20), 50))
        ranked = sorted(matches, key=lambda p: len(p["answer"]), reverse=True)

        return {
            "total_matches": len(matches),
            "returned": min(limit, len(matches)),
            "filter": {"code": code, "theme": theme, "product": product, "country": country, "product_type": product_type},
            "questions": [
                {
                    "id": p["id"],
                    "question": p["question"][:400],
                    "answer_preview": p["answer"][:400],
                }
                for p in ranked[:limit]
            ],
        }

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------
    def aggregate(
        self,
        group_by: str,
        code: str | None = None,
        theme: str | None = None,
        product: str | None = None,
        country: str | None = None,
        product_type: str | None = None,
        limit: int = 15,
    ) -> dict[str, Any]:
        """Count pairs grouped by product, country, date, or product_type."""
        matches = self.find(code=code, theme=theme, product=product, country=country, product_type=product_type)
        counter: collections.Counter[str] = collections.Counter()

        for p in matches:
            if group_by == "product":
                val = (p.get("product") or "").strip()
            elif group_by == "country":
                val = (p.get("country") or "").strip()
            elif group_by == "date":
                raw_date = (p.get("date") or "").strip()
                val = raw_date[:7] if len(raw_date) >= 7 else raw_date
            elif group_by == "product_type":
                val = (p.get("doc_product_type") or "").strip()
            else:
                val = ""
            if val:
                counter[val] += 1

        limit = max(1, min(int(limit or 15), 50))
        ranked = counter.most_common(limit)

        return {
            "group_by": group_by,
            "filter": {"code": code, "theme": theme, "product": product, "country": country, "product_type": product_type},
            "total_matching_pairs": len(matches),
            "groups_returned": len(ranked),
            "groups": [{"value": v, "count": c} for v, c in ranked],
        }

    # ------------------------------------------------------------------
    # Code catalog lookup
    # ------------------------------------------------------------------
    def lookup_code(self, code: str) -> dict[str, str] | None:
        """Return catalog entry for a CTD code, or None.

        Tries exact match first, then strips the module prefix, then tries
        prefix matching (P.8.3 finds P.8.3.01) picking the shortest suffix.
        """
        code = code.strip()
        entry = self.code_catalog.get(code)
        if entry:
            return entry
        bare = MODULE_PREFIX.sub("", code).strip(".")
        entry = self.code_catalog.get(bare)
        if entry:
            return entry
        prefix = bare + "."
        candidates = [
            (k, v) for k, v in self.code_catalog.items()
            if k.startswith(prefix)
        ]
        if candidates:
            candidates.sort(key=lambda kv: kv[0])
            return candidates[0][1]
        return None

    def available_themes(self) -> list[str]:
        return [self.theme_names[k] for k in sorted(self.theme_names)]

    def corpus_summary(self) -> dict[str, Any]:
        """Return key aggregate statistics about the corpus."""
        total = len(self.pairs)
        sm_count = sum(1 for p in self.pairs if "biotech" not in (p.get("doc_product_type") or "").lower())
        lm_count = sum(1 for p in self.pairs if "biotech" in (p.get("doc_product_type") or "").lower())
        products = set((p.get("product") or "").strip() for p in self.pairs if (p.get("product") or "").strip())
        countries = set((p.get("country") or "").strip() for p in self.pairs if (p.get("country") or "").strip() and (p.get("country") or "").strip() != "N/A")
        doc_ids = set(str(p.get("doc_id") or "") for p in self.pairs if p.get("doc_id"))
        return {
            "total_pairs": total,
            "total_documents": len(doc_ids),
            "small_molecule_count": sm_count,
            "large_molecule_count": lm_count,
            "unique_products": len(products),
            "unique_countries": len(countries),
            "top_products": collections.Counter((p.get("product") or "").strip() for p in self.pairs if (p.get("product") or "").strip()).most_common(10),
            "top_countries": collections.Counter((p.get("country") or "").strip() for p in self.pairs if (p.get("country") or "").strip() and (p.get("country") or "").strip() != "N/A").most_common(10),
        }


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

    code_catalog = _load_code_catalog()
    return Corpus(pairs, theme_by_id, theme_names, code_catalog)
