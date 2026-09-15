#!/usr/bin/env python3
"""Precompute corpus statistics from the enriched 269-row JSON file.

Reads cmc_response_documents (3).json and outputs corpus_stats.json used
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
INPUT_PATH = DASHBOARD_DIR / "cmc_response_documents (3).json"
CODE_CATALOG_PATH = DASHBOARD_DIR / "code_catalog.json"
OUTPUT_PATH = DASHBOARD_DIR / "corpus_stats.json"


def _is_large(product_type: str) -> bool:
    return "Biotech" in product_type


def _parse_individual_codes(codes_str: str) -> list[str]:
    """Split a semicolon/comma-separated codes string into individual codes."""
    if not codes_str or not codes_str.strip():
        return []
    parts = re.split(r"[;,]+", codes_str)
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

    # --- CTD S-sections (Drug Substance) ---
    s_counts: dict[str, int] = collections.Counter()
    p_counts: dict[str, int] = collections.Counter()
    ar_counts: dict[str, int] = collections.Counter()

    for r in rows:
        codes = _parse_individual_codes(r.get("codes", ""))
        for c in codes:
            # Normalize to parent section (e.g. S.4.1.01 -> S.4.1)
            # Keep up to 3 numeric parts: S.x.y
            m = re.match(r"^([SPARsp])\.(\d+(?:\.\d+)?)", c)
            if not m:
                continue
            prefix = m.group(1).upper()
            section = f"{prefix}.{m.group(2)}"
            if prefix == "S":
                s_counts[section] += 1
            elif prefix == "P":
                p_counts[section] += 1
            elif prefix in ("A", "R"):
                ar_counts[section] += 1

    stats["ctd_s_sections"] = [
        {"code": k, "count": v}
        for k, v in sorted(s_counts.items(), key=lambda x: _code_sort_key(x[0]))
    ]
    stats["ctd_p_sections"] = [
        {"code": k, "count": v}
        for k, v in sorted(p_counts.items(), key=lambda x: _code_sort_key(x[0]))
    ]
    stats["ctd_ar_sections"] = [
        {"code": k, "count": v}
        for k, v in sorted(ar_counts.items(), key=lambda x: _code_sort_key(x[0]))
    ]

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

    # --- CTD Characteristics (code -> products bubble chart) ---
    code_products: dict[str, set[str]] = {}
    for r in rows:
        prod = (r.get("doc_product") or "").strip()
        if not prod:
            continue
        codes = _parse_individual_codes(r.get("codes", ""))
        for c in codes:
            m = re.match(r"^([SPARsp])\.(\d+(?:\.\d+)?)", c)
            if not m:
                continue
            section = f"{m.group(1).upper()}.{m.group(2)}"
            code_products.setdefault(section, set()).add(prod)

    stats["ctd_characteristics"] = [
        {"code": k, "product_count": len(v), "products": sorted(v)}
        for k, v in sorted(code_products.items(), key=lambda x: _code_sort_key(x[0]))
    ]

    # --- Code summary: SM vs LM split per CTD code ---
    code_sm: dict[str, int] = collections.Counter()
    code_lm: dict[str, int] = collections.Counter()
    code_sm_prods: dict[str, set[str]] = {}
    code_lm_prods: dict[str, set[str]] = {}

    for r in rows:
        codes = _parse_individual_codes(r.get("codes", ""))
        prod = (r.get("doc_product") or "").strip()
        is_lm = _is_large(r.get("doc_product_type", ""))
        for c in codes:
            section = _normalize_code(c)
            if not section:
                continue
            if is_lm:
                code_lm[section] += 1
                code_lm_prods.setdefault(section, set()).add(prod)
            else:
                code_sm[section] += 1
                code_sm_prods.setdefault(section, set()).add(prod)

    all_codes = sorted(set(code_sm) | set(code_lm), key=_code_sort_key)
    code_summary = []
    for code in all_codes:
        sm = code_sm.get(code, 0)
        lm = code_lm.get(code, 0)
        title = ""
        # Try exact match first, then parent codes with common suffixes
        for suffix in ["", ".01", ".1"]:
            cat_entry = catalog.get(code + suffix)
            if cat_entry:
                title = cat_entry.get("title", "")
                break
        code_summary.append({
            "code": code,
            "title": title,
            "sm_count": sm,
            "lm_count": lm,
            "total": sm + lm,
            "sm_products": sorted(code_sm_prods.get(code, set())),
            "lm_products": sorted(code_lm_prods.get(code, set())),
        })
    stats["code_summary"] = code_summary

    return stats


def main() -> None:
    if not INPUT_PATH.exists():
        print(f"ERROR: {INPUT_PATH} not found", file=sys.stderr)
        sys.exit(1)

    with open(INPUT_PATH, encoding="utf-8") as f:
        rows = json.load(f)

    print(f"Loaded {len(rows)} rows from {INPUT_PATH.name}")
    stats = build_stats(rows)

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
