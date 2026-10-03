#!/usr/bin/env python3
"""Precompute corpus statistics from the enriched dataset.

Reads cmc_response_documents (4).json and outputs corpus_stats.json used
exclusively by the Corpus Insights section (section 10) of the dashboard.

Output: cmc-dashboard/corpus_stats.json
"""

from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path
from typing import Any

DASHBOARD_DIR = Path(__file__).resolve().parent.parent.parent
INPUT_PATH = DASHBOARD_DIR / "cmc_response_documents (4).json"
CODE_CATALOG_PATH = DASHBOARD_DIR / "code_catalog.json"
OUTPUT_PATH = DASHBOARD_DIR / "corpus_stats.json"


def _is_large(product_type: str) -> bool:
    return "Biotech" in product_type


def _parse_individual_codes(codes_val: str | list) -> list[str]:
    """Extract individual codes from the codes field (string or list)."""
    if isinstance(codes_val, list):
        return [str(c).strip() for c in codes_val if str(c).strip() and not str(c).strip().lower().startswith("n.a")]
    if not codes_val or not codes_val.strip():
        return []
    parts = re.split(r"[;,]+", codes_val)
    return [p.strip() for p in parts if p.strip() and not p.strip().startswith("n.a")]


def _code_sort_key(code: str) -> tuple:
    """Sort S.1.2.01 as (S, 1, 2, 1) for natural ordering."""
    parts = re.findall(r"[A-Za-z]+|\d+", code)
    result = []
    for p in parts:
        try:
            result.append((0, int(p)))
        except ValueError:
            result.append((1, p))
    return tuple(result)


def _load_code_catalog() -> dict[str, dict[str, Any]]:
    """Load code_catalog.json into a dict keyed by code."""
    if not CODE_CATALOG_PATH.exists():
        return {}
    with open(CODE_CATALOG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    catalog: dict[str, dict[str, Any]] = {}
    for entry in data.get("body", []):
        if entry.get("code"):
            catalog[entry["code"]] = entry
    return catalog


def _normalize_code(raw: str) -> str | None:
    """Normalize a raw code string to its parent section (e.g. S.4.1.01 -> S.4.1)."""
    m = re.match(r"^([SPARsp])\.(\d+(?:\.\d+)?)", raw)
    if not m:
        return None
    return f"{m.group(1).upper()}.{m.group(2)}"


# Canonical X-axis subsections for Drug Substance / Drug Product / Appendices & Regional.
CTD_S_CANONICAL = [
    "S.1",
    "S.2.1", "S.2.2", "S.2.3", "S.2.4", "S.2.5", "S.2.6",
    "S.3.1", "S.3.2",
    "S.4.1", "S.4.2", "S.4.3", "S.4.4", "S.4.5",
    "S.5", "S.6",
    "S.7.1", "S.7.2", "S.7.3",
]
CTD_P_CANONICAL = [
    "P.1",
    "P.2.1", "P.2.2", "P.2.3", "P.2.4", "P.2.5", "P.2.6",
    "P.3.1", "P.3.2", "P.3.3", "P.3.4", "P.3.5",
    "P.4.1", "P.4.2", "P.4.3", "P.4.4", "P.4.5", "P.4.6",
    "P.5.1", "P.5.2", "P.5.3", "P.5.4", "P.5.5", "P.5.6",
    "P.6", "P.7",
    "P.8.1", "P.8.2", "P.8.3",
]
CTD_AR_CANONICAL = [
    "A.1", "A.2", "A.3",
    "R.1", "R.2",
    "R.3.1", "R.3.2", "R.3.3", "R.3.5",
    "R.5", "R.6", "R.7", "R.8", "R.9", "R.10",
]
_CTD_LEAF_PARENTS = frozenset({
    "S.1", "S.5", "S.6",
    "P.1", "P.6", "P.7",
    "A.1", "A.2", "A.3",
    "R.1", "R.2", "R.5", "R.6", "R.7", "R.8", "R.9", "R.10",
})
_CTD_S_SET = frozenset(CTD_S_CANONICAL)
_CTD_P_SET = frozenset(CTD_P_CANONICAL)
_CTD_AR_SET = frozenset(CTD_AR_CANONICAL)


def _normalize_numeric_parts(code: str) -> str | None:
    """Uppercase prefix and strip leading zeros from numeric segments (S.7.01 -> S.7.1)."""
    m = re.match(r"^([SPARsp])\.([\d.]+)$", code.strip())
    if not m:
        return None
    prefix = m.group(1).upper()
    parts = [str(int(p)) for p in m.group(2).split(".") if p.isdigit()]
    if not parts:
        return None
    return f"{prefix}.{'.'.join(parts)}"


def _map_to_canonical_ctd(raw: str) -> str | None:
    """Map a raw CTD code onto the fixed S/P/A/R subsection axis, or None if unmatched."""
    normalized = _normalize_numeric_parts(raw)
    if not normalized:
        return None
    prefix = normalized[0]
    if prefix == "S":
        allowed = _CTD_S_SET
    elif prefix == "P":
        allowed = _CTD_P_SET
    elif prefix in ("A", "R"):
        allowed = _CTD_AR_SET
    else:
        return None

    parts = normalized.split(".")
    # Leaf parents roll up any deeper codes (A.1.02 -> A.1, R.5.01 -> R.5).
    if len(parts) >= 2:
        leaf = f"{parts[0]}.{parts[1]}"
        if leaf in _CTD_LEAF_PARENTS:
            return leaf if leaf in allowed else None

    # Two-level subsection (R.3.3.xx -> R.3.3)
    if len(parts) >= 3:
        two_level = f"{parts[0]}.{parts[1]}.{parts[2]}"
        if two_level in allowed:
            return two_level

    # Exact match on normalized code (already a leaf or listed subsection)
    if normalized in allowed:
        return normalized

    return None


def _canonical_counts(counter: collections.Counter[str], canonical: list[str]) -> list[dict[str, Any]]:
    """Emit fixed-order subsection rows, including zeros."""
    return [{"code": code, "count": int(counter.get(code, 0))} for code in canonical]


_STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "is", "are", "be",
    "was", "were", "with", "that", "this", "these", "those", "it", "its", "as",
    "at", "by", "on", "from", "please", "provide", "submit", "we", "our", "you",
    "your", "has", "have", "had", "will", "shall", "not", "no", "any", "all",
    "can", "may", "should", "would", "section", "data", "information", "provided",
    "also", "been", "per", "than", "into", "which", "were", "their", "there",
    "such", "more", "most", "other", "same", "each", "between", "following",
    "question", "response", "bayer", "applicant", "product", "drug", "regarding",
}


def _extract_key_themes(questions: list[str], top_n: int = 5) -> list[str]:
    """Extract the most frequent meaningful 2-word phrases from questions."""
    bigram_counter: dict[str, int] = collections.Counter()
    word_counter: dict[str, int] = collections.Counter()

    for q in questions:
        words = re.findall(r"[a-z]{3,}", q.lower())
        meaningful = [w for w in words if w not in _STOP_WORDS]
        word_counter.update(meaningful)
        for i in range(len(meaningful) - 1):
            bigram_counter[f"{meaningful[i]} {meaningful[i+1]}"] += 1

    top_bigrams = [bg for bg, _ in bigram_counter.most_common(top_n) if bigram_counter[bg] >= 2]
    if len(top_bigrams) < top_n:
        top_words = [w for w, _ in word_counter.most_common(top_n * 2) if w not in " ".join(top_bigrams)]
        top_bigrams.extend(top_words[:top_n - len(top_bigrams)])

    return top_bigrams[:top_n]


def _build_code_summary(
    code: str,
    title: str,
    sm_count: int,
    lm_count: int,
    sm_products: list[str],
    lm_products: list[str],
    questions: list[str],
) -> str:
    """Build a concise 2-3 line summary for a CTD code row."""
    total = sm_count + lm_count
    all_products = sm_products + lm_products

    themes = _extract_key_themes(questions, top_n=4)
    theme_str = ", ".join(themes[:3]) if themes else "general CMC topics"

    line1 = f"Covers {total} questions"
    if title:
        line1 += f" on {title.strip().rstrip('.')}"
    line1 += "."

    if sm_count and lm_count:
        line2 = f"SM ({sm_count}) and LM ({lm_count}) both represented."
    elif sm_count:
        line2 = f"Exclusively Small Molecule ({sm_count} Qs)."
    else:
        line2 = f"Exclusively Large Molecule ({lm_count} Qs)."

    line3 = f"Key themes: {theme_str}."

    return f"{line1} {line2} {line3}"


def build_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    catalog = _load_code_catalog()

    # --- KPIs ---
    total_questions = len(rows)
    unique_doc_ids = set(str(r.get("doc_id", "")) for r in rows if r.get("doc_id"))
    total_documents = len(unique_doc_ids)

    sm_count = sum(1 for r in rows if not _is_large(r.get("doc_product_type", "")))
    lm_count = sum(1 for r in rows if _is_large(r.get("doc_product_type", "")))

    stats["kpis"] = {
        "total_documents": total_documents,
        "total_questions": total_questions,
        "small_molecule_questions": sm_count,
        "large_molecule_questions": lm_count,
    }

    # --- Questions per product (alphabetical) ---
    product_counts: dict[str, int] = collections.Counter()
    for r in rows:
        prod = (r.get("doc_product") or "").strip()
        if prod:
            product_counts[prod] += 1
    stats["questions_per_product"] = [
        {"product": k, "count": v}
        for k, v in sorted(product_counts.items(), key=lambda x: x[0].lower())
    ]

    # --- Questions per country ---
    country_counts: dict[str, int] = collections.Counter()
    for r in rows:
        country = (r.get("doc_country") or "").strip()
        if country and country != "N/A":
            country_counts[country] += 1
    stats["questions_per_country"] = [
        {"country": k, "count": v}
        for k, v in sorted(country_counts.items(), key=lambda x: -x[1])
    ]

    # --- CTD S/P/A/R sections (canonical subsections on X-axis) ---
    s_counts: dict[str, int] = collections.Counter()
    p_counts: dict[str, int] = collections.Counter()
    ar_counts: dict[str, int] = collections.Counter()

    for r in rows:
        codes = _parse_individual_codes(r.get("codes", ""))
        for c in codes:
            mapped = _map_to_canonical_ctd(c)
            if not mapped:
                continue
            if mapped.startswith("S."):
                s_counts[mapped] += 1
            elif mapped.startswith("P."):
                p_counts[mapped] += 1
            elif mapped.startswith("A.") or mapped.startswith("R."):
                ar_counts[mapped] += 1

    stats["ctd_s_sections"] = _canonical_counts(s_counts, CTD_S_CANONICAL)
    stats["ctd_p_sections"] = _canonical_counts(p_counts, CTD_P_CANONICAL)
    stats["ctd_ar_sections"] = _canonical_counts(ar_counts, CTD_AR_CANONICAL)

    # --- Questions per year (total, small, large) ---
    year_data: dict[str, dict[str, int]] = {}
    year_docs: dict[str, dict[str, set]] = {}

    for r in rows:
        date = (r.get("doc_approve_date") or "").strip()
        if not date or len(date) < 4:
            continue
        year = date[:4]
        if year not in year_data:
            year_data[year] = {"total": 0, "small": 0, "large": 0}
        if year not in year_docs:
            year_docs[year] = {"total": set(), "small": set(), "large": set()}

        year_data[year]["total"] += 1
        doc_id = str(r.get("doc_id", ""))

        if _is_large(r.get("doc_product_type", "")):
            year_data[year]["large"] += 1
            if doc_id:
                year_docs[year]["large"].add(doc_id)
        else:
            year_data[year]["small"] += 1
            if doc_id:
                year_docs[year]["small"].add(doc_id)

        if doc_id:
            year_docs[year]["total"].add(doc_id)

    stats["questions_per_year"] = [
        {"year": y, **year_data[y]}
        for y in sorted(year_data.keys())
    ]
    stats["documents_per_year"] = [
        {
            "year": y,
            "total": len(year_docs[y]["total"]),
            "small": len(year_docs[y]["small"]),
            "large": len(year_docs[y]["large"]),
        }
        for y in sorted(year_docs.keys())
    ]

    # --- CTD Characteristics bubble data (canonical S/P subsections only) ---
    s_products: dict[str, set[str]] = {c: set() for c in CTD_S_CANONICAL}
    p_products: dict[str, set[str]] = {c: set() for c in CTD_P_CANONICAL}

    for r in rows:
        prod = (r.get("doc_product") or "").strip()
        if not prod:
            continue
        for c in _parse_individual_codes(r.get("codes", "")):
            mapped = _map_to_canonical_ctd(c)
            if not mapped:
                continue
            if mapped in s_products:
                s_products[mapped].add(prod)
            elif mapped in p_products:
                p_products[mapped].add(prod)

    stats["ctd_characteristics_s"] = [
        {"code": code, "product_count": len(s_products[code]), "products": sorted(s_products[code])}
        for code in CTD_S_CANONICAL
    ]
    stats["ctd_characteristics_p"] = [
        {"code": code, "product_count": len(p_products[code]), "products": sorted(p_products[code])}
        for code in CTD_P_CANONICAL
    ]

    # --- Code summary: SM vs LM split per CTD code ---
    code_sm: dict[str, int] = collections.Counter()
    code_lm: dict[str, int] = collections.Counter()
    code_sm_prods: dict[str, set[str]] = {}
    code_lm_prods: dict[str, set[str]] = {}
    code_questions: dict[str, list[str]] = {}

    for r in rows:
        codes = _parse_individual_codes(r.get("codes", ""))
        prod = (r.get("doc_product") or "").strip()
        is_lm = _is_large(r.get("doc_product_type", ""))
        question = (r.get("Question/Consideration") or "").strip()
        seen_sections: set[str] = set()
        for c in codes:
            section = _normalize_code(c)
            if not section or section in seen_sections:
                continue
            seen_sections.add(section)
            if is_lm:
                code_lm[section] += 1
                code_lm_prods.setdefault(section, set()).add(prod)
            else:
                code_sm[section] += 1
                code_sm_prods.setdefault(section, set()).add(prod)
            if question:
                code_questions.setdefault(section, []).append(question)

    all_codes = sorted(set(code_sm) | set(code_lm), key=_code_sort_key)
    code_summary = []
    for code in all_codes:
        sm = code_sm.get(code, 0)
        lm = code_lm.get(code, 0)
        title = ""
        for suffix in ["", ".01", ".1"]:
            cat_entry = catalog.get(code + suffix)
            if cat_entry:
                title = cat_entry.get("title", "")
                break
        summary = _build_code_summary(
            code, title, sm, lm,
            sorted(code_sm_prods.get(code, set())),
            sorted(code_lm_prods.get(code, set())),
            code_questions.get(code, []),
        )
        code_summary.append({
            "code": code,
            "title": title,
            "sm_count": sm,
            "lm_count": lm,
            "total": sm + lm,
            "sm_products": sorted(code_sm_prods.get(code, set())),
            "lm_products": sorted(code_lm_prods.get(code, set())),
            "summary": summary,
        })
    stats["code_summary"] = code_summary

    return stats


def main() -> None:
    if not INPUT_PATH.exists():
        print(f"ERROR: {INPUT_PATH} not found", file=sys.stderr)
        sys.exit(1)

    # Load raw rows for KPIs (total count matches the file)
    with open(INPUT_PATH, encoding="utf-8") as f:
        raw_rows = json.load(f)

    # Load deduplicated pairs (same as chatbot uses) for code_summary
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.rag import load_qna_pairs
    deduped_pairs = load_qna_pairs(INPUT_PATH)

    # Convert deduped pairs back to raw-row format for build_stats
    converted = []
    for p in deduped_pairs:
        converted.append({
            "doc_id": p.get("doc_id", ""),
            "doc_product": p.get("product", ""),
            "doc_country": p.get("country", ""),
            "doc_product_type": p.get("doc_product_type", ""),
            "doc_approve_date": p.get("date", ""),
            "doc_name": p.get("doc_name", ""),
            "codes": p.get("codes", []),
            "keywords": p.get("keywords", []),
            "Question/Consideration": p.get("question", ""),
            "Answer/Response": p.get("answer", ""),
        })

    print(f"Loaded {len(raw_rows)} raw rows, {len(converted)} deduplicated pairs from {INPUT_PATH.name}")
    stats = build_stats(converted)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print(f"Wrote {OUTPUT_PATH}")
    kpis = stats["kpis"]
    print(f"  Documents: {kpis['total_documents']}")
    print(f"  Questions: {kpis['total_questions']} (SM: {kpis['small_molecule_questions']}, LM: {kpis['large_molecule_questions']})")
    print(f"  Products:  {len(stats['questions_per_product'])}")
    print(f"  S-codes:   {len(stats['ctd_s_sections'])}")
    print(f"  P-codes:   {len(stats['ctd_p_sections'])}")
    print(f"  Code summary: {len(stats['code_summary'])} codes (SM/LM split)")
    print(f"  Years:     {[y['year'] for y in stats['questions_per_year']]}")


if __name__ == "__main__":
    main()
