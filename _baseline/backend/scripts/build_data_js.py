#!/usr/bin/env python3
"""Regenerate data.js and metrics.json from the unified dataset.

Reads cmc_response_documents (4).json and outputs:
  - data.js (window.CMC_DATA = {...})
  - metrics.json (same object, plain JSON)

These files power Sections 00–09 of the dashboard.
"""

from __future__ import annotations

import collections
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow importing canonical CTD mapping from sibling script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_corpus_stats import _map_to_canonical_ctd  # noqa: E402

DASHBOARD_DIR = Path(__file__).resolve().parent.parent.parent
INPUT_PATH = DASHBOARD_DIR / "cmc_response_documents (4).json"
OUTPUT_JS = DASHBOARD_DIR / "data.js"
OUTPUT_JSON = DASHBOARD_DIR / "metrics.json"


def _section_from_canonical(code: str) -> str:
    """High-level CTD section from a canonical subsection (P.8.3 -> P.8)."""
    parts = code.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return code


def _build_recommendations(
    questions: list[dict[str, Any]],
    *,
    total_questions: int,
    brief: int,
    empty: int,
    ha_analysis: list[dict[str, Any]],
    product_analysis: list[dict[str, Any]],
    top_keywords: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """TRD/RFI-style recommendations: priority, decision, owner, root cause."""
    code_stats: dict[str, dict[str, Any]] = {}
    for r in questions:
        product = (r.get("doc_product") or "").strip()
        country = (r.get("doc_country") or "").strip()
        if country == "N/A":
            country = ""
        for raw in _parse_codes(r.get("codes", "")):
            canon = _map_to_canonical_ctd(raw) or raw
            bucket = code_stats.setdefault(
                canon,
                {
                    "questions": 0,
                    "products": set(),
                    "countries": set(),
                    "raw_codes": collections.Counter(),
                },
            )
            bucket["questions"] += 1
            if product:
                bucket["products"].add(product)
            if country:
                bucket["countries"].add(country)
            bucket["raw_codes"][raw] += 1

    ranked = sorted(
        code_stats.items(),
        key=lambda item: (
            -item[1]["questions"],
            -len(item[1]["products"]),
            -len(item[1]["countries"]),
        ),
    )

    recs: list[dict[str, Any]] = []

    # 1–2: Systemic multi-HA / multi-product CTD clusters → Monitor / SME review
    systemic = [
        (code, st)
        for code, st in ranked
        if st["questions"] >= 8
        and len(st["products"]) >= 2
        and len(st["countries"]) >= 2
    ]
    for code, st in systemic[:2]:
        n_prod = len(st["products"])
        n_ha = len(st["countries"])
        top_raw = st["raw_codes"].most_common(1)[0][0] if st["raw_codes"] else code
        section = _section_from_canonical(code)
        if st["questions"] >= 40 and n_prod >= 5 and n_ha >= 5:
            priority, decision = "High", "SME review required"
        elif st["questions"] >= 20:
            priority, decision = "High", "Monitor trend"
        else:
            priority, decision = "Medium", "Monitor trend"
        recs.append(
            {
                "title": f"Review recurring questions in {code}",
                "detail": (
                    f"{st['questions']} questions map to {code} across {n_prod} products "
                    f"and {n_ha} Health Authorities. Recurrence across products/HAs warrants "
                    f"hierarchical TRD review (section {section} → granular profile code), "
                    f"not a one-off dossier fix."
                ),
                "priority": priority,
                "decision": decision,
                "ctd_section": section,
                "trd_code": top_raw if top_raw != code else code,
                "root_cause": "Evolving regulatory expectation / possible profile gap",
                "owner": "RA CMC / TRD Profile Owner",
                "action_type": "SME review",
                "recurring": "Recurring",
            }
        )

    # 3: Product-concentrated code → product-specific, no general update
    product_specific = [
        (code, st)
        for code, st in ranked
        if st["questions"] >= 10 and len(st["products"]) == 1
    ]
    if product_specific:
        code, st = product_specific[0]
        prod = next(iter(st["products"]))
        section = _section_from_canonical(code)
        top_raw = st["raw_codes"].most_common(1)[0][0] if st["raw_codes"] else code
        recs.append(
            {
                "title": f"Keep {code} product-specific ({prod})",
                "detail": (
                    f"{st['questions']} questions for {code} concentrate on a single product "
                    f"({prod}). Classify as product-specific; do not trigger a general TRD "
                    f"profile update unless the same pattern appears on other products."
                ),
                "priority": "Medium",
                "decision": "No, product-specific issue only",
                "ctd_section": section,
                "trd_code": top_raw if top_raw != code else code,
                "root_cause": "Product-specific technical issue",
                "owner": "RA CMC / Pharmaceutical Development",
                "action_type": "monitoring",
                "recurring": "Recurring",
            }
        )

    # 4: Top HA concentration → regional readiness (not automatic profile update)
    top_ha = ha_analysis[0] if ha_analysis else None
    if top_ha:
        recs.append(
            {
                "title": f"Strengthen {top_ha['ha']} regional readiness",
                "detail": (
                    f"{top_ha['ha']} drives {top_ha['questions']} questions "
                    f"({top_ha['share_pct']}% of corpus) across {top_ha['documents']} documents. "
                    f"Treat as regional expectation / response playbook work unless the same "
                    f"CTD topics recur across multiple HAs."
                ),
                "priority": "High" if top_ha["share_pct"] >= 8 else "Medium",
                "decision": "Monitor trend",
                "ctd_section": "R / Regional",
                "trd_code": "—",
                "root_cause": "Regional expectation",
                "owner": "Regulatory Strategy / RA CMC",
                "action_type": "checklist update",
                "recurring": "Recurring",
            }
        )

    # 5: Theme cluster → template / checklist, conservative on profile update
    if top_keywords:
        themes = ", ".join(k["keyword"] for k in top_keywords[:5])
        top_theme = top_keywords[0]
        recs.append(
            {
                "title": f"Cluster theme: {top_theme['keyword']}",
                "detail": (
                    f"Top deficiency themes: {themes}. Build reusable justification blocks "
                    f"and author checklists. Recommend a TRD profile change only if the theme "
                    f"maps to a silent/ambiguous profile after hierarchical CTD→TRD mapping."
                ),
                "priority": "Medium",
                "decision": "Monitor trend",
                "ctd_section": "Cross-cutting",
                "trd_code": "—",
                "root_cause": "Inadequate justification / authoring guidance",
                "owner": "RA CMC / TRD Profile Owner",
                "action_type": "template update",
                "recurring": "Recurring",
            }
        )

    # 6: Brief/empty answers → dossier execution / SME review
    weak = brief + empty
    if weak > 0:
        pct = round(100 * weak / max(total_questions, 1), 1)
        recs.append(
            {
                "title": "Close answer-completeness gaps",
                "detail": (
                    f"{weak} questions have empty or very brief answers ({pct}%). "
                    f"This points to dossier execution / SME completeness, not an automatic "
                    f"TRD profile update. Flag for SME review before any profile change."
                ),
                "priority": "High" if pct >= 10 else "Medium",
                "decision": "No, dossier execution issue",
                "ctd_section": "Cross-cutting",
                "trd_code": "—",
                "root_cause": "Insufficient authoring execution",
                "owner": "RA CMC / Quality Control",
                "action_type": "SME review",
                "recurring": "Recurring",
            }
        )

    # 7: Highest-volume product → depth focus, still product-scoped
    top_prod = product_analysis[0] if product_analysis else None
    if top_prod and len(recs) < 7:
        recs.append(
            {
                "title": f"Deep-dive CMC readiness for {top_prod['product']}",
                "detail": (
                    f"{top_prod['product']} accounts for {top_prod['questions']} questions "
                    f"({top_prod['share_pct']}%) across {top_prod['documents']} documents. "
                    f"Prioritize product-level dossier quality; escalate to TRD profile update "
                    f"only when the same CTD subsection also recurs on other products."
                ),
                "priority": "Medium",
                "decision": "No, product-specific issue only",
                "ctd_section": "Cross-cutting",
                "trd_code": "—",
                "root_cause": "Product-specific technical issue",
                "owner": "RA CMC / Manufacturing",
                "action_type": "training",
                "recurring": "Recurring",
            }
        )

    return recs[:7]


def _parse_codes(codes_val) -> list[str]:
    if isinstance(codes_val, list):
        return [str(c).strip() for c in codes_val if str(c).strip() and not str(c).strip().lower().startswith("n.a")]
    if not codes_val or not str(codes_val).strip():
        return []
    parts = re.split(r"[;,]+", str(codes_val))
    return [p.strip() for p in parts if p.strip() and not p.strip().lower().startswith("n.a")]


def _parse_keywords(kw_val) -> list[str]:
    if isinstance(kw_val, list):
        return [str(k).strip() for k in kw_val if str(k).strip()]
    if not kw_val or not str(kw_val).strip():
        return []
    return [k.strip() for k in re.split(r"[;,\n]+", str(kw_val)) if k.strip()]


def _quarter(date_str: str) -> str | None:
    if not date_str or len(date_str) < 7:
        return None
    try:
        month = int(date_str[5:7])
        q = (month - 1) // 3 + 1
        return f"{date_str[:4]}-Q{q}"
    except (ValueError, IndexError):
        return None


def build(rows: list[dict[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}

    total_rows = len(rows)
    questions = [r for r in rows if (r.get("Question/Consideration") or "").strip()]
    total_questions = len(questions)

    doc_ids = set(str(r.get("doc_id", "")) for r in rows if r.get("doc_id"))
    countries = set(
        (r.get("doc_country") or "").strip()
        for r in rows
        if (r.get("doc_country") or "").strip() and (r.get("doc_country") or "").strip() != "N/A"
    )
    products = set(
        (r.get("doc_product") or "").strip()
        for r in rows
        if (r.get("doc_product") or "").strip()
    )
    pages = [r.get("doc_pages", 0) or 0 for r in rows if r.get("doc_pages")]
    avg_pages = round(sum(pages) / max(len(pages), 1), 1)

    answers = [(r.get("Answer/Response") or "").strip() for r in questions]
    substantive = sum(1 for a in answers if len(a) > 100)
    brief = sum(1 for a in answers if 0 < len(a) <= 100)
    empty = sum(1 for a in answers if len(a) == 0)

    dates = [(r.get("doc_approve_date") or "").strip() for r in rows if (r.get("doc_approve_date") or "").strip()]
    date_range = {"min": min(dates) if dates else "", "max": max(dates) if dates else ""}

    data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    data["source_file"] = INPUT_PATH.name
    data["kpis"] = {
        "total_rows": total_rows,
        "total_questions": total_questions,
        "unique_documents": len(doc_ids),
        "unique_countries": len(countries),
        "unique_products": len(products),
        "avg_questions_per_doc": round(total_questions / max(len(doc_ids), 1), 2),
        "date_range": date_range,
        "status": {"analyzed": total_rows, "extraction_in_progress": 0, "error": 0},
        "answer_quality": {"substantive": substantive, "brief": brief, "empty": empty},
        "avg_pages": avg_pages,
    }

    # --- Questions per country ---
    country_counter: collections.Counter[str] = collections.Counter()
    for r in questions:
        c = (r.get("doc_country") or "").strip()
        if c and c != "N/A":
            country_counter[c] += 1
    data["questions_per_country"] = [
        {"country": c, "questions": n}
        for c, n in country_counter.most_common()
    ]

    # --- HA analysis (country + doc + avg) ---
    country_docs: dict[str, set[str]] = {}
    for r in questions:
        c = (r.get("doc_country") or "").strip()
        doc_id = str(r.get("doc_id", ""))
        if c and c != "N/A":
            country_docs.setdefault(c, set()).add(doc_id)
    data["ha_analysis"] = sorted(
        [
            {
                "ha": c,
                "questions": country_counter[c],
                "documents": len(country_docs.get(c, set())),
                "avg_q_per_doc": round(country_counter[c] / max(len(country_docs.get(c, set())), 1), 2),
                "share_pct": round(100 * country_counter[c] / max(total_questions, 1), 1),
            }
            for c in country_counter
        ],
        key=lambda x: -x["questions"],
    )

    # --- Product analysis ---
    product_counter: collections.Counter[str] = collections.Counter()
    product_docs: dict[str, set[str]] = {}
    for r in questions:
        p = (r.get("doc_product") or "").strip()
        doc_id = str(r.get("doc_id", ""))
        if p:
            product_counter[p] += 1
            product_docs.setdefault(p, set()).add(doc_id)
    data["product_analysis"] = sorted(
        [
            {
                "product": p,
                "questions": product_counter[p],
                "documents": len(product_docs.get(p, set())),
                "avg_q_per_doc": round(product_counter[p] / max(len(product_docs.get(p, set())), 1), 2),
                "share_pct": round(100 * product_counter[p] / max(total_questions, 1), 1),
            }
            for p in product_counter
        ],
        key=lambda x: -x["questions"],
    )

    # --- Top keywords ---
    keyword_counter: collections.Counter[str] = collections.Counter()
    for r in questions:
        kws = _parse_keywords(r.get("keywords", ""))
        keyword_counter.update(kws)
    data["top_keywords"] = [
        {"keyword": k, "count": n} for k, n in keyword_counter.most_common(20)
    ]

    # --- Top codes ---
    code_counter: collections.Counter[str] = collections.Counter()
    for r in questions:
        codes = _parse_codes(r.get("codes", ""))
        code_counter.update(codes)
    data["top_codes"] = [
        {"code": c, "count": n} for c, n in code_counter.most_common(15)
    ]

    # --- Quarterly ---
    quarter_counter: collections.Counter[str] = collections.Counter()
    for r in questions:
        q = _quarter((r.get("doc_approve_date") or "").strip())
        if q:
            quarter_counter[q] += 1
    data["quarterly"] = [
        {"quarter": q, "questions": quarter_counter[q]}
        for q in sorted(quarter_counter.keys())
    ]

    # --- Monthly ---
    month_counter: collections.Counter[str] = collections.Counter()
    for r in questions:
        d = (r.get("doc_approve_date") or "").strip()
        if d and len(d) >= 7:
            month_counter[d[:7]] += 1
    monthly = []
    prev = None
    for m in sorted(month_counter.keys()):
        cnt = month_counter[m]
        mom = round(100 * (cnt - prev) / prev, 1) if prev and prev > 0 else None
        monthly.append({"month": m, "questions": cnt, "mom_pct": mom})
        prev = cnt
    data["monthly"] = monthly

    # --- Quarterly top countries ---
    quarter_country: dict[str, collections.Counter[str]] = {}
    for r in questions:
        q = _quarter((r.get("doc_approve_date") or "").strip())
        c = (r.get("doc_country") or "").strip()
        if q and c and c != "N/A":
            quarter_country.setdefault(q, collections.Counter())[c] += 1
    data["quarterly_top_countries"] = {
        q: [{"country": c, "questions": n} for c, n in counter.most_common(8)]
        for q, counter in sorted(quarter_country.items())
    }

    # --- Findings (summary bullets) ---
    top_ha = data["ha_analysis"][0] if data["ha_analysis"] else None
    top_prod = data["product_analysis"][0] if data["product_analysis"] else None
    top_kw = data["top_keywords"][0] if data["top_keywords"] else None
    top_code = data["top_codes"][0] if data["top_codes"] else None

    densest_doc = ""
    densest_count = 0
    doc_q_count: collections.Counter[str] = collections.Counter()
    for r in questions:
        dn = (r.get("doc_name") or "").strip()
        if dn:
            doc_q_count[dn] += 1
    if doc_q_count:
        densest_doc, densest_count = doc_q_count.most_common(1)[0]

    data["findings"] = [
        {"label": "Most active HA", "value": f"{top_ha['ha']} ({top_ha['questions']} Q)" if top_ha else "N/A"},
        {"label": "Highest-volume product", "value": f"{top_prod['product']} ({top_prod['questions']} Q)" if top_prod else "N/A"},
        {"label": "Top keyword theme", "value": f"{top_kw['keyword']} ({top_kw['count']})" if top_kw else "N/A"},
        {"label": "Top CTD code", "value": f"{top_code['code']} ({top_code['count']})" if top_code else "N/A"},
        {"label": "Densest RFI document", "value": f"{densest_doc} — {densest_count} questions"},
        {"label": "Avg questions / document", "value": data["kpis"]["avg_questions_per_doc"]},
        {"label": "Median document pages", "value": int(sorted(pages)[len(pages) // 2]) if pages else 0},
        {"label": "Analysis coverage", "value": f"{total_rows} analyzed / {total_rows} rows"},
    ]

    # --- Heatmap (all countries × all products) ---
    all_countries_list = [x["ha"] for x in data["ha_analysis"]]
    all_products_list = [x["product"] for x in data["product_analysis"]]
    country_index = {c: i for i, c in enumerate(all_countries_list)}
    product_index = {p: i for i, p in enumerate(all_products_list)}
    matrix = [[0] * len(all_products_list) for _ in range(len(all_countries_list))]
    for r in questions:
        c = (r.get("doc_country") or "").strip()
        p = (r.get("doc_product") or "").strip()
        ci = country_index.get(c)
        pi = product_index.get(p)
        if ci is not None and pi is not None:
            matrix[ci][pi] += 1
    data["heatmap"] = {
        "countries": all_countries_list,
        "products": all_products_list,
        "matrix": matrix,
    }

    # --- Top country-product pairs ---
    cp_counter: collections.Counter[tuple[str, str]] = collections.Counter()
    for r in questions:
        c = (r.get("doc_country") or "").strip()
        p = (r.get("doc_product") or "").strip()
        if c and c != "N/A" and p:
            cp_counter[(c, p)] += 1
    data["top_country_product_pairs"] = [
        {"country": c, "product": p, "questions": n}
        for (c, p), n in cp_counter.most_common(15)
    ]

    # --- Chart helpers ---
    data["chart_country_all"] = [
        {"country": x["ha"], "questions": x["questions"]}
        for x in data["ha_analysis"]
    ]
    data["chart_country_top15"] = data["chart_country_all"][:15]
    data["chart_product_all"] = [
        {"product": x["product"], "questions": x["questions"]}
        for x in data["product_analysis"]
    ]
    data["chart_product_top12"] = data["chart_product_all"][:12]

    # --- Recent documents ---
    sorted_rows = sorted(rows, key=lambda r: (r.get("doc_approve_date") or ""), reverse=True)
    seen_docs: set[str] = set()
    recent = []
    for r in sorted_rows:
        dn = (r.get("doc_name") or "").strip()
        if dn and dn not in seen_docs:
            seen_docs.add(dn)
            recent.append({
                "name": dn,
                "country": (r.get("doc_country") or "").strip(),
                "product": (r.get("doc_product") or "").strip(),
                "date": (r.get("doc_approve_date") or "").strip(),
                "pages": r.get("doc_pages", 0) or 0,
                "questions": doc_q_count.get(dn, 0),
                "status": "analyzed",
            })
            if len(recent) >= 12:
                break
    data["recent_documents"] = recent

    # --- Recommendations (TRD / RFI evaluation framework) ---
    data["recommendations"] = _build_recommendations(
        questions,
        total_questions=total_questions,
        brief=brief,
        empty=empty,
        ha_analysis=data["ha_analysis"],
        product_analysis=data["product_analysis"],
        top_keywords=data["top_keywords"],
    )

    # --- Languages ---
    data["languages"] = [{"language": "English", "documents": len(doc_ids)}]

    return data


def main() -> None:
    if not INPUT_PATH.exists():
        print(f"ERROR: {INPUT_PATH} not found", file=sys.stderr)
        sys.exit(1)

    with open(INPUT_PATH, encoding="utf-8") as f:
        rows = json.load(f)

    print(f"Loaded {len(rows)} rows from {INPUT_PATH.name}")
    result = build(rows)

    # Write data.js
    js_content = f"window.CMC_DATA = {json.dumps(result, ensure_ascii=False)};\n"
    OUTPUT_JS.write_text(js_content, encoding="utf-8")
    print(f"Wrote {OUTPUT_JS}")

    # Write metrics.json
    OUTPUT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON}")

    kpis = result["kpis"]
    print(f"  Rows: {kpis['total_rows']}")
    print(f"  Questions: {kpis['total_questions']}")
    print(f"  Documents: {kpis['unique_documents']}")
    print(f"  Countries: {kpis['unique_countries']}")
    print(f"  Products: {kpis['unique_products']}")
    print(f"  Date range: {kpis['date_range']['min']} to {kpis['date_range']['max']}")


if __name__ == "__main__":
    main()
