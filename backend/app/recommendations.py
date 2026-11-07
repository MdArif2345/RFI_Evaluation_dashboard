"""TRD profile gap analysis over recurring authority questions.

Pipeline, one TRD code at a time so no call carries the whole corpus:
  1. select questions approved inside the window (default 12 months)
  2. group by granular TRD code; keep codes that recur across documents
  3. LLM: understand and cluster each code's questions, tag the request type
  4. LLM: compare the clusters with the TRD profile governing that code
  5. persist to-dos, covered topics and exclusions for the run

Exclusions: clarification/explanation and administrative requests never become
to-dos, nor do single-document clusters, nor topics the profile already
clearly requires.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

from . import trd_profiles
from .chat_db import _db, _now
from .config import Settings
from .corpus import get_corpus, rollup_code
from .rag import chat_client

PROMPT_VERSION = "trd-gap-v2"
BATCH_SIZE = 40
QUESTION_CHARS = 600
PROFILE_CHARS = 20_000
MAX_CODES = 150
WORKERS = 3

REQUEST_TYPES = (
    "new_requirement",
    "standard_data_request",
    "clarification_or_explanation",
    "product_specific",
    "administrative",
)
EXCLUDED_TYPES = {"clarification_or_explanation", "administrative"}
DECISIONS = (
    "Yes, update profile",
    "No, profile already sufficient",
    "No, dossier execution issue",
    "No, product-specific issue only",
    "Monitor trend",
    "SME review required",
)
TODO_DECISIONS = {"Yes, update profile", "SME review required", "Monitor trend"}
PRIORITIES = ("Critical", "High", "Medium", "Low", "Monitor")
CONFIDENCE = ("High", "Medium", "Low")

_BAD = {"", "n.a.", "n/a", "na", "none", "-"}
_db_lock = threading.Lock()
_run_lock = threading.Lock()
_active_run: int | None = None

CLUSTER_PROMPT = """You are an experienced Regulatory Affairs CMC Manager analysing Health Authority
questions (RFIs / deficiency letters) that were all mapped to TRD profile code {code}
({code_title}).

Group the questions below into topic clusters: questions asking for the same kind of
information belong together, even across products and authorities. For each cluster
classify the request type as exactly one of:
- new_requirement: the authority expects content, data, justification or detail that a
  writing guideline could require up front
- standard_data_request: routine request for data that is normally part of the package
  (stability, batch data, specifications, validation reports)
- clarification_or_explanation: the authority only asks to clarify, confirm or correct
  something already submitted (typos, inconsistent values between sections, "please
  confirm", "please explain the discrepancy") and does NOT ask for new data, a
  justification, a rationale, a study or additional content. Requests to justify, provide,
  submit, include or demonstrate something are new_requirement or standard_data_request.
- product_specific: driven by one product's own technical situation
- administrative: forms, certificates, GMP/licence evidence, fees, local formalities

{existing}Return JSON only:
{{"clusters": [{{"topic": "short topic name", "summary": "what the authorities asked, 1-2 sentences",
"request_type": "<one of the types>", "question_ids": ["q1", "q2"]}}]}}
Every question id must appear in exactly one cluster.

Questions:
{questions}"""

GAP_PROMPT = """You are an experienced Regulatory Affairs CMC Manager deciding whether the TRD writing
profile for {code} ({code_title}) needs updating to prevent recurring Health Authority
questions in future submissions.

{profile_block}

Recurring question clusters for this code (questions approved {period}):
{clusters}

Rules:
- Do NOT recommend a profile update merely because an authority asked for standard
  information that the profile already clearly requires. Then the decision is
  "No, profile already sufficient" (or "No, dossier execution issue" if the dossier
  omitted required content), with a short verbatim quote from the profile.
- Recommend "Yes, update profile" only when the profile is silent, ambiguous, too generic
  or outdated on what the authorities repeatedly ask for.
- Suggested wording must be concrete text that could be pasted into the profile, not
  "add more detail".
- Be conservative; do not overfit one product or one authority.
- If no profile text is provided, say what the profile should contain and treat the
  conclusion as provisional.

Return JSON only:
{{"results": [{{"cluster": <cluster number>, "title": "imperative to-do, max 12 words",
"profile_coverage": "yes|partial|no|unknown", "profile_quote": "verbatim quote or empty",
"gap": "what is missing from the profile, 1-2 sentences",
"decision": "<one of: {decisions}>",
"root_cause": "e.g. Profile missing requirement; Profile unclear; Profile too generic; Insufficient authoring execution; Missing data at submission; Regional expectation; Evolving regulatory expectation; Product-specific technical issue; No action needed",
"suggested_wording": "text to add to the profile, or empty",
"priority": "Critical|High|Medium|Low|Monitor",
"owner": "e.g. RA CMC, Analytical Development, Stability, TRD Profile Owner",
"action_type": "profile update|training|checklist update|template update|SME review|monitoring",
"confidence": "High|Medium|Low"}}]}}
One result per cluster number."""


class AnalysisBusy(RuntimeError):
    pass


# ----- data selection -----

def _codes_of(row: dict[str, Any]) -> list[str]:
    for field in ("question_codes", "codes"):
        raw = row.get(field)
        items = raw if isinstance(raw, list) else re.split(r"[;,\n]+", str(raw or ""))
        codes = []
        for c in items:
            c = trd_profiles.clean_code(str(c))
            if c.lower() not in _BAD and trd_profiles.CODE_SHAPE.match(c) and c not in codes:
                codes.append(c)
        if codes:
            return codes
    return []


def _clean(value: Any) -> str:
    v = " ".join(str(value or "").split())
    return "" if v.lower() in _BAD else v


def select_window(
    settings: Settings,
    months: int | None = None,
    start: date | None = None,
    end: date | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Questions approved between `start` and `end` (inclusive), grouped by granular TRD code.

    Without explicit dates the window is the last `months` months up to today.
    """
    end = end or today or date.today()
    start = start or (end - timedelta(days=round((months or 12) * 30.44)))
    cutoff, last_day = start.isoformat(), end.isoformat()
    rows = json.loads(settings.qna_path.read_text(encoding="utf-8"))
    questions: dict[str, dict[str, Any]] = {}
    by_code: dict[str, list[str]] = {}
    unmapped = 0
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        day = _clean(row.get("doc_approve_date"))[:10]
        text = _clean(row.get("Question/Consideration"))
        if not day or day < cutoff or day > last_day or len(text) < 15:
            continue
        qid = f"q{idx}"
        codes = _codes_of(row)
        questions[qid] = {
            "id": qid,
            "question": text,
            "doc_id": str(row.get("doc_id") or ""),
            "doc_name": _clean(row.get("doc_name")),
            "product": _clean(row.get("doc_product")),
            "country": _clean(row.get("doc_country")),
            "date": day,
            "keywords": _clean(row.get("keywords")),
            "codes": codes,
        }
        if not codes:
            unmapped += 1
        for code in codes:
            by_code.setdefault(code, []).append(qid)
    return {"cutoff": cutoff, "today": last_day, "questions": questions, "by_code": by_code, "unmapped": unmapped}


def recurring_codes(window: dict[str, Any], min_questions: int, min_documents: int) -> list[dict[str, Any]]:
    out = []
    for code, qids in window["by_code"].items():
        docs = {window["questions"][q]["doc_id"] for q in qids}
        if len(qids) >= min_questions and len(docs) >= min_documents:
            out.append({"code": code, "questions": len(qids), "documents": len(docs)})
    out.sort(key=lambda c: (-c["questions"], -c["documents"], c["code"]))
    return out


def code_title(code: str) -> str:
    corpus = get_corpus()
    entry = corpus.lookup_code(code) or corpus.lookup_code(rollup_code(code))
    return (entry or {}).get("title") or "title not in catalog"


# ----- LLM plumbing -----

def _cache_get(key: str) -> Any | None:
    with _db_lock:
        row = _db().execute("SELECT output FROM analysis_cache WHERE hash = ?", (key,)).fetchone()
    return json.loads(row["output"]) if row else None


def _cache_put(key: str, stage: str, output: Any) -> None:
    with _db_lock:
        db = _db()
        db.execute(
            "INSERT OR REPLACE INTO analysis_cache (hash, stage, output, created_at) VALUES (?, ?, ?, ?)",
            (key, stage, json.dumps(output, ensure_ascii=False), _now()),
        )
        db.commit()


def _parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model did not return JSON")
    return json.loads(text[start : end + 1])


def _llm_json(settings: Settings, stage: str, prompt: str, max_tokens: int) -> dict[str, Any]:
    key = hashlib.sha256(f"{PROMPT_VERSION}|{settings.chat_model()}|{stage}|{prompt}".encode("utf-8")).hexdigest()
    cached = _cache_get(key)
    if cached is not None:
        return {"data": cached, "cached": True}
    client = chat_client(settings)
    kwargs: dict[str, Any] = {
        "model": settings.chat_model(),
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
    except Exception:
        resp = client.chat.completions.create(**kwargs)
    data = _parse_json(resp.choices[0].message.content or "")
    _cache_put(key, stage, data)
    return {"data": data, "cached": False}


# ----- stages -----

def _cluster_code(settings: Settings, code: str, title: str, qids: list[str], questions: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    clusters: dict[str, dict[str, Any]] = {}
    all_cached = True
    for start in range(0, len(qids), BATCH_SIZE):
        batch = qids[start : start + BATCH_SIZE]
        lines = [
            f"[{q}] ({questions[q]['product'] or 'unknown product'}; {questions[q]['country'] or 'unknown HA'}) "
            f"{questions[q]['question'][:QUESTION_CHARS]}"
            for q in batch
        ]
        existing = ""
        if clusters:
            existing = (
                "Clusters already found in earlier batches; reuse the exact topic name when a question "
                "fits one of them:\n" + "\n".join(f"- {c['topic']} ({c['request_type']})" for c in clusters.values()) + "\n\n"
            )
        out = _llm_json(
            settings,
            "cluster",
            CLUSTER_PROMPT.format(code=code, code_title=title, existing=existing, questions="\n".join(lines)),
            3000,
        )
        all_cached &= out["cached"]
        allowed = set(batch)
        assigned: set[str] = set()
        for c in out["data"].get("clusters") or []:
            ids = [i for i in (c.get("question_ids") or []) if i in allowed and i not in assigned]
            if not ids:
                continue
            assigned.update(ids)
            topic = _clean(c.get("topic"))[:120] or "Other"
            rtype = c.get("request_type") if c.get("request_type") in REQUEST_TYPES else "standard_data_request"
            slot = clusters.setdefault(topic.lower(), {"topic": topic, "summary": _clean(c.get("summary"))[:600], "request_type": rtype, "question_ids": []})
            slot["question_ids"].extend(ids)
        leftover = [q for q in batch if q not in assigned]
        if leftover:
            slot = clusters.setdefault("\0unclustered", {"topic": "Unclustered", "summary": "Questions the model did not assign to a topic.", "request_type": "clarification_or_explanation", "unclustered": True, "question_ids": []})
            slot["question_ids"].extend(leftover)
    return list(clusters.values()), all_cached


def _enrich(cluster: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
    qs = [questions[q] for q in cluster["question_ids"]]
    products = sorted({q["product"] for q in qs if q["product"]})
    countries = sorted({q["country"] for q in qs if q["country"]})
    docs = sorted({q["doc_id"] for q in qs})
    return {
        **cluster,
        "questions": len(qs),
        "documents": len(docs),
        "products": products,
        "health_authorities": countries,
        "first_date": min(q["date"] for q in qs),
        "last_date": max(q["date"] for q in qs),
        "evidence": [
            {k: q[k] for k in ("id", "doc_name", "product", "country", "date")} | {"question": q["question"][:500]}
            for q in sorted(qs, key=lambda x: x["date"], reverse=True)
        ],
    }


def _gap_code(settings: Settings, code: str, title: str, clusters: list[dict[str, Any]], profile: dict[str, Any] | None, period: str) -> tuple[list[dict[str, Any]], bool]:
    if profile:
        profile_block = (
            f"TRD profile text (file {profile['filename']}, mapped to {profile['matched_code']}):\n"
            f"<<<\n{profile['content'][:PROFILE_CHARS]}\n>>>"
        )
    else:
        profile_block = "No TRD profile text was provided for this code."
    parts = []
    for n, c in enumerate(clusters, start=1):
        samples = "\n".join(f"    - {e['question'][:300]}" for e in c["evidence"][:3])
        parts.append(
            f"Cluster {n}: {c['topic']} [{c['request_type']}]\n"
            f"  {c['questions']} questions, {c['documents']} documents, products: {', '.join(c['products'][:6]) or 'n/a'}; "
            f"authorities: {', '.join(c['health_authorities'][:6]) or 'n/a'}\n"
            f"  Summary: {c['summary']}\n  Examples:\n{samples}"
        )
    out = _llm_json(
        settings,
        "gap",
        GAP_PROMPT.format(code=code, code_title=title, profile_block=profile_block, period=period, clusters="\n\n".join(parts), decisions="; ".join(DECISIONS)),
        4000,
    )
    by_n = {}
    for r in out["data"].get("results") or []:
        try:
            by_n[int(r.get("cluster"))] = r
        except (TypeError, ValueError):
            continue
    return [by_n.get(n, {}) for n in range(1, len(clusters) + 1)], out["cached"]


def _normalise_result(r: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    def pick(value: Any, allowed: tuple[str, ...], default: str) -> str:
        return value if value in allowed else default

    res = {
        "title": _clean(r.get("title"))[:160],
        "profile_coverage": pick(r.get("profile_coverage"), ("yes", "partial", "no", "unknown"), "unknown"),
        "profile_quote": _clean(r.get("profile_quote"))[:600],
        "gap": _clean(r.get("gap"))[:800],
        "decision": pick(r.get("decision"), DECISIONS, "SME review required"),
        "root_cause": _clean(r.get("root_cause"))[:160],
        "suggested_wording": str(r.get("suggested_wording") or "").strip()[:2000],
        "priority": pick(r.get("priority"), PRIORITIES, "Medium"),
        "owner": _clean(r.get("owner"))[:120] or "TRD Profile Owner",
        "action_type": _clean(r.get("action_type"))[:60],
        "confidence": pick(r.get("confidence"), CONFIDENCE, "Low"),
        "provisional": profile is None,
        "profile_file": profile["filename"] if profile else "",
        "profile_code": profile["matched_code"] if profile else "",
    }
    if profile is None:
        res["profile_coverage"] = "unknown"
        res["profile_quote"] = ""
        if res["decision"] in ("Yes, update profile", "No, profile already sufficient"):
            res["decision"] = "SME review required"
        if res["priority"] == "Critical":
            res["priority"] = "High"
    elif res["profile_coverage"] == "yes" and res["decision"] in TODO_DECISIONS:
        res["decision"] = "No, profile already sufficient"
    return res


# ----- run store -----

def _save_run(run_id: int, **fields: Any) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in fields.values()]
    with _db_lock:
        db = _db()
        db.execute(f"UPDATE analysis_runs SET {sets} WHERE id = ?", (*values, run_id))
        db.commit()


def _save_items(run_id: int, code: str, items: list[tuple[str, dict[str, Any], dict[str, Any] | None]]) -> None:
    with _db_lock:
        db = _db()
        db.executemany(
            "INSERT INTO analysis_items (run_id, code, kind, cluster, result) VALUES (?, ?, ?, ?, ?)",
            [
                (run_id, code, kind, json.dumps(cluster, ensure_ascii=False), json.dumps(result, ensure_ascii=False) if result else None)
                for kind, cluster, result in items
            ],
        )
        db.commit()


def mark_interrupted_runs() -> None:
    with _db_lock:
        db = _db()
        db.execute(
            "UPDATE analysis_runs SET status = 'failed', error = 'Server restarted during the run', finished_at = ? "
            "WHERE status IN ('queued', 'running')",
            (_now(),),
        )
        db.commit()


def _run_row(row: Any) -> dict[str, Any]:
    out = dict(row)
    for k in ("params", "progress", "summary"):
        out[k] = json.loads(out[k]) if out.get(k) else None
    return out


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    with _db_lock:
        rows = _db().execute("SELECT * FROM analysis_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_run_row(r) for r in rows]


def get_run(run_id: int, with_items: bool = True) -> dict[str, Any] | None:
    with _db_lock:
        row = _db().execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
        items = (
            _db().execute("SELECT code, kind, cluster, result FROM analysis_items WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
            if row and with_items
            else []
        )
    if not row:
        return None
    run = _run_row(row)
    if with_items:
        run["items"] = [
            {"code": i["code"], "kind": i["kind"], "cluster": json.loads(i["cluster"]), "result": json.loads(i["result"]) if i["result"] else None}
            for i in items
        ]
    return run


def latest_run_id(finished_only: bool = False) -> int | None:
    sql = "SELECT id FROM analysis_runs " + ("WHERE status = 'done' " if finished_only else "") + "ORDER BY id DESC LIMIT 1"
    with _db_lock:
        row = _db().execute(sql).fetchone()
    return row["id"] if row else None


# ----- orchestration -----

def _analyse_code(settings: Settings, run_id: int, code: str, window: dict[str, Any], params: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, int]:
    questions = window["questions"]
    title = code_title(code)
    raw_clusters, c_cached = _cluster_code(settings, code, title, window["by_code"][code], questions)
    clusters = [_enrich(c, questions) | {"code": code, "code_title": title, "ctd_section": rollup_code(code)} for c in raw_clusters]

    items: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
    to_compare = []
    for c in clusters:
        if c.get("unclustered"):
            items.append(("excluded", c, {"reason": "not assigned to a topic by the model"}))
        elif c["request_type"] in EXCLUDED_TYPES:
            items.append(("excluded", c, {"reason": c["request_type"].replace("_", " ")}))
        elif c["documents"] < params["min_documents"]:
            items.append(("excluded", c, {"reason": "not recurring (single document)" if c["documents"] == 1 else "below document threshold"}))
        else:
            to_compare.append(c)

    g_cached = True
    if to_compare:
        period = f"from {window['cutoff']} to {window['today']}"
        results, g_cached = _gap_code(settings, code, title, to_compare, profile, period)
        for c, r in zip(to_compare, results):
            res = _normalise_result(r, profile)
            if not res["title"]:
                res["title"] = c["topic"]
            kind = "todo" if res["decision"] in TODO_DECISIONS else "covered"
            items.append((kind, c, res))

    _save_items(run_id, code, items)
    counts = {"clusters": len(clusters), "cached": int(c_cached and g_cached)}
    for kind, c, r in items:
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "excluded":
            if c.get("unclustered"):
                key = "excluded_unclustered"
            elif c["request_type"] in EXCLUDED_TYPES:
                key = "excluded_clarification"
            else:
                key = "excluded_not_recurring"
            counts[key] = counts.get(key, 0) + 1
    return counts


def _run(settings: Settings, run_id: int, params: dict[str, Any]) -> None:
    global _active_run
    try:
        window = select_window(
            settings,
            months=params["months"],
            start=date.fromisoformat(params["start_date"]) if params.get("start_date") else None,
            end=date.fromisoformat(params["end_date"]) if params.get("end_date") else None,
        )
        all_codes = recurring_codes(window, params["min_questions"], params["min_documents"])
        profiles = trd_profiles._all_with_content()
        profile_map = {c["code"]: trd_profiles.profile_for_code(c["code"], profiles) for c in all_codes}
        if params.get("scope", "all") == "profiled":
            codes = [c for c in all_codes if profile_map[c["code"]]]
            if not codes:
                raise RuntimeError(
                    "No uploaded profile matches a recurring code in this window. Upload a profile, "
                    "check its codes, or choose 'All recurring codes'."
                )
        else:
            codes = all_codes[:MAX_CODES]
        summary: dict[str, Any] = {
            "scope": params.get("scope", "all"),
            "window_start": window["cutoff"],
            "window_end": window["today"],
            "questions_in_window": len(window["questions"]),
            "unmapped_questions": window["unmapped"],
            "recurring_codes_total": len(all_codes),
            "recurring_codes": len(codes),
            "codes_done": 0,
            "codes_failed": 0,
            "codes_from_cache": 0,
            "clusters": 0,
            "todo": 0,
            "covered": 0,
            "excluded": 0,
            "excluded_clarification": 0,
            "excluded_not_recurring": 0,
            "excluded_unclustered": 0,
            "codes_without_profile": [c["code"] for c in codes if not profile_map[c["code"]]],
            "model": settings.chat_model(),
        }
        _save_run(run_id, status="running", progress={"done": 0, "total": len(codes)}, summary=summary)
        lock = threading.Lock()

        def work(entry: dict[str, Any]) -> None:
            code = entry["code"]
            try:
                counts = _analyse_code(settings, run_id, code, window, params, profile_map[code])
                with lock:
                    summary["codes_done"] += 1
                    summary["codes_from_cache"] += counts.pop("cached", 0)
                    for k, v in counts.items():
                        summary[k] = summary.get(k, 0) + v
            except Exception as exc:
                _save_items(run_id, code, [("error", {"code": code, "topic": code, "questions": entry["questions"]}, {"error": str(exc)[:500]})])
                with lock:
                    summary["codes_failed"] += 1
            with lock:
                done = summary["codes_done"] + summary["codes_failed"]
                _save_run(run_id, progress={"done": done, "total": len(codes), "current": code}, summary=summary)

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(work, codes))

        failed_all = codes and summary["codes_failed"] == len(codes)
        _save_run(
            run_id,
            status="failed" if failed_all else "done",
            error="Every code failed; check the LLM settings in backend/.env" if failed_all else None,
            finished_at=_now(),
            summary=summary,
        )
    except Exception as exc:
        _save_run(run_id, status="failed", error=str(exc)[:500], finished_at=_now())
    finally:
        with _run_lock:
            _active_run = None


def start_run(
    settings: Settings,
    months: int,
    min_questions: int,
    min_documents: int,
    started_by: str,
    scope: str = "profiled",
    start_date: date | None = None,
    end_date: date | None = None,
) -> int:
    global _active_run
    params = {
        "months": months,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "min_questions": min_questions,
        "min_documents": min_documents,
        "scope": scope,
    }
    with _run_lock:
        if _active_run is not None:
            raise AnalysisBusy(f"Run {_active_run} is still in progress.")
        with _db_lock:
            db = _db()
            cur = db.execute(
                "INSERT INTO analysis_runs (status, params, progress, started_by, started_at) VALUES ('queued', ?, ?, ?, ?)",
                (json.dumps(params), json.dumps({"done": 0, "total": 0}), started_by[:80], _now()),
            )
            db.commit()
            run_id = int(cur.lastrowid)
        _active_run = run_id
    threading.Thread(target=_run, args=(settings, run_id, params), daemon=True, name=f"trd-gap-{run_id}").start()
    return run_id


# ----- export -----

CSV_COLUMNS = [
    "RFI/Question ID", "Product", "Health Authority", "High-Level CTD Section",
    "Granular TRD Profile Code", "Topic Cluster", "Recurring or New", "Root Cause",
    "Profile Update Needed", "Recommended Action", "Priority", "Owner", "Confidence",
    "SME Review Needed",
]


def export_csv(run: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    order = {p: i for i, p in enumerate(PRIORITIES)}
    rows = [i for i in run.get("items", []) if i["kind"] in ("todo", "covered")]
    rows.sort(key=lambda i: (i["kind"] != "todo", order.get(i["result"]["priority"], 9), -i["cluster"]["questions"]))
    for item in rows:
        c, r = item["cluster"], item["result"]
        docs = sorted({e["doc_name"] for e in c["evidence"] if e["doc_name"]})
        action = r["suggested_wording"] or r["gap"]
        writer.writerow([
            "; ".join(docs),
            "; ".join(c["products"]),
            "; ".join(c["health_authorities"]),
            c["ctd_section"],
            c["code"],
            c["topic"],
            "Recurring" if c["documents"] >= 2 else "New",
            r["root_cause"],
            "Yes" if r["decision"] == "Yes, update profile" else ("Provisional" if r["provisional"] else "No"),
            f"{r['title']}: {action}" if action else r["title"],
            r["priority"],
            r["owner"],
            r["confidence"],
            "Yes" if r["decision"] == "SME review required" or r["provisional"] else "No",
        ])
    return buf.getvalue()
