#!/usr/bin/env python3
"""Build per-year dashboard + corpus stats for the Yearly Overview page.

Reads cmc_response_documents (4).json and writes yearly_overview.json:

  {
    "years": ["2023", "2024", ...],
    "by_year": {
      "2025": { "dashboard": {...}, "corpus": {...} }
    }
  }

Year is taken from doc_approve_date[:4].
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

DASHBOARD_DIR = Path(__file__).resolve().parent.parent.parent
INPUT_PATH = DASHBOARD_DIR / "cmc_response_documents (4).json"
OUTPUT_PATH = DASHBOARD_DIR / "yearly_overview.json"

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent))

from build_corpus_stats import build_stats  # noqa: E402
from build_data_js import build as build_dashboard  # noqa: E402


def _year_of(row: dict[str, Any]) -> str | None:
    date = (row.get("doc_approve_date") or "").strip()
    if len(date) >= 4 and date[:4].isdigit():
        return date[:4]
    return None


def _rows_for_year(rows: list[dict[str, Any]], year: str) -> list[dict[str, Any]]:
    return [r for r in rows if _year_of(r) == year]


def _to_corpus_rows(deduped_pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for p in deduped_pairs:
        converted.append(
            {
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
            }
        )
    return converted


def _slim_dashboard(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields needed by Yearly Overview sections."""
    return {
        "kpis": data.get("kpis", {}),
        "ha_analysis": data.get("ha_analysis", []),
        "product_analysis": data.get("product_analysis", []),
        "top_codes": data.get("top_codes", []),
        "chart_country_top15": data.get("chart_country_top15", []),
        "chart_country_all": data.get("chart_country_all", data.get("chart_country_top15", [])),
        "chart_product_top12": data.get("chart_product_top12", []),
        "questions_per_country": data.get("questions_per_country", []),
    }


def _slim_corpus(stats: dict[str, Any]) -> dict[str, Any]:
    """Corpus insights without nested per-year charts (page is already year-scoped)."""
    return {
        "kpis": stats.get("kpis", {}),
        "questions_per_product": stats.get("questions_per_product", []),
        "questions_per_country": stats.get("questions_per_country", []),
        "ctd_s_sections": stats.get("ctd_s_sections", []),
        "ctd_p_sections": stats.get("ctd_p_sections", []),
        "ctd_ar_sections": stats.get("ctd_ar_sections", []),
        "ctd_characteristics_s": stats.get("ctd_characteristics_s", []),
        "ctd_characteristics_p": stats.get("ctd_characteristics_p", []),
        "code_summary": stats.get("code_summary", []),
    }


def main() -> None:
    if not INPUT_PATH.exists():
        print(f"ERROR: {INPUT_PATH} not found", file=sys.stderr)
        sys.exit(1)

    with open(INPUT_PATH, encoding="utf-8") as f:
        raw_rows = json.load(f)

    from app.rag import load_qna_pairs

    deduped_pairs = load_qna_pairs(INPUT_PATH)
    corpus_rows = _to_corpus_rows(deduped_pairs)

    years = sorted(
        {
            y
            for y in (_year_of(r) for r in raw_rows)
            if y
        }
    )
    if not years:
        print("ERROR: no approve-date years found", file=sys.stderr)
        sys.exit(1)

    by_year: dict[str, Any] = {}
    for year in years:
        dash_rows = _rows_for_year(raw_rows, year)
        corp_rows = _rows_for_year(corpus_rows, year)
        dash = _slim_dashboard(build_dashboard(dash_rows))
        corp = _slim_corpus(build_stats(corp_rows))
        by_year[year] = {"dashboard": dash, "corpus": corp}
        print(
            f"  {year}: dashboard Q={dash['kpis'].get('total_questions', 0)} "
            f"corpus Q={corp['kpis'].get('total_questions', 0)}"
        )

    payload = {"years": years, "by_year": by_year, "source_file": INPUT_PATH.name}
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    print(f"  Years: {years}")


if __name__ == "__main__":
    main()
