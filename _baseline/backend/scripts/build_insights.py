#!/usr/bin/env python3
"""Precompute clustering and corpus analytics for the dashboard insights section.

Reuses the embeddings already stored in Chroma by scripts/ingest.py, so nothing
is re-embedded and no corpus text leaves the machine except short cluster
summaries sent to myGenAssist for naming.

Output: cmc-dashboard/qna_insights.json
"""

from __future__ import annotations

import collections
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import DASHBOARD_DIR, get_settings  # noqa: E402
from app.corpus import extract_codes  # noqa: E402
from app.rag import chat_client, get_collection, load_qna_pairs  # noqa: E402

OUTPUT_PATH = DASHBOARD_DIR / "qna_insights.json"
NUM_CLUSTERS = 12
NEAR_DUP_THRESHOLD = 0.92

STOP_WORDS = set(
    """the a an and or of to in for is are be been was were with that this these those it its
    as at by on from please provide submit we our you your bayer response question applicant
    has have had will shall not no any all can may should would section source document
    documents data information provided attached refer see below above following table figure
    page new amended unchanged number version also been per than into which were their there
    such more most other same each between during under within about after before""".split()
)

# Checked in order; first match wins so each question gets exactly one label.
INTENT_RULES = [
    ("Justify", r"\bjustif(?:y|ication)\b"),
    ("Clarify", r"\bclarif(?:y|ication)\b"),
    ("Confirm", r"\bconfirm\b"),
    ("Explain", r"\bexplain\b|\bexplanation\b"),
    ("Update / amend", r"\bupdat(?:e|ed)\b|\brevis(?:e|ed)\b|\bamend(?:ed)?\b|\bcorrect(?:ed)?\b"),
    ("Submit", r"\bsubmit(?:ted)?\b"),
    ("Provide", r"\bprovide\b|\bprovid(?:ing)\b|\binclude\b|\bspecify\b|\bindicate\b|\bstate\b"),
]

# Also checked in order; most specific posture first.
POSTURE_RULES = [
    ("Handled locally / N.A.", r"handled locally|not applicable|no action required"),
    (
        "Commitment - data to follow",
        r"will be available|studies have been started|stability commitment|will be provided|"
        r"will be submitted|upon availability|post-approval",
    ),
    (
        "Dossier amended",
        r"has been included|have been included|has been updated|have been updated|"
        r"has been revised|have been revised|has been amended|have been amended|"
        r"correction has been|updated \w+\.\d",
    ),
    (
        "Justified - no change",
        r"we propose to keep|no further|not required|no change|we consider|"
        r"is considered (?:justified|acceptable)|remains? valid",
    ),
    ("Document provided", r"please refer|source:|is provided|are provided|find attached|attached"),
    ("Acknowledged", r"acknowledge|we note|is noted|noted that"),
]


def classify(text: str, rules: list[tuple[str, str]], default: str) -> str:
    low = text.lower()
    for label, pattern in rules:
        if re.search(pattern, low):
            return label
    return default


def spherical_kmeans(
    matrix: np.ndarray, k: int, *, seed: int = 0, iterations: int = 50
) -> tuple[np.ndarray, np.ndarray]:
    """k-means++ init on unit vectors; cosine similarity as the distance."""
    rng = np.random.default_rng(seed)
    centers = [matrix[rng.integers(len(matrix))]]
    for _ in range(k - 1):
        gap = np.maximum(1.0 - (matrix @ np.array(centers).T).max(axis=1), 0.0) ** 2
        total = gap.sum()
        probs = gap / total if total > 0 else None
        centers.append(matrix[rng.choice(len(matrix), p=probs)])

    centroids = np.array(centers, dtype=np.float32)
    labels = np.zeros(len(matrix), dtype=int)
    for _ in range(iterations):
        new_labels = (matrix @ centroids.T).argmax(axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for c in range(k):
            members = matrix[labels == c]
            if len(members):
                mean = members.mean(axis=0)
                centroids[c] = mean / (np.linalg.norm(mean) + 1e-9)

    return labels, centroids


def top_terms(texts: list[str], limit: int = 8) -> list[str]:
    counter: collections.Counter[str] = collections.Counter()
    for text in texts:
        for word in re.findall(r"[a-z]{4,}", text.lower()):
            if word not in STOP_WORDS:
                counter[word] += 1
    return [w for w, _ in counter.most_common(limit)]


def fallback_name(cluster: dict[str, Any]) -> str:
    return ", ".join(cluster["terms"][:3]).title()


def name_clusters(clusters: list[dict[str, Any]]) -> None:
    """Ask MGA for a short human label per cluster; fall back to top terms."""
    settings = get_settings()

    try:
        client = chat_client(settings)
    except Exception as exc:
        print(f"  MGA unavailable ({exc}); using term-based names")
        for cluster in clusters:
            cluster["name"] = fallback_name(cluster)
        return

    for cluster in clusters:
        samples = "\n".join(f"- {q[:200]}" for q in cluster["examples"])
        prompt = (
            "These CMC regulatory questions were grouped into one topic.\n\n"
            f"Frequent terms: {', '.join(cluster['terms'])}\n"
            f"Example questions:\n{samples}\n\n"
            "Reply with ONLY a short topic label of 2 to 4 words, no punctuation, "
            "no quotes, title case. Example: Stability And Shelf Life"
        )

        # No max_tokens: this MGA model spends budget on hidden reasoning first and
        # returns finish_reason=length with empty content when the cap is low.
        label = ""
        for _ in range(2):
            try:
                completion = client.chat.completions.create(
                    model=settings.chat_model(),
                    temperature=0.0,
                    messages=[{"role": "user", "content": prompt}],
                )
                candidate = (completion.choices[0].message.content or "").strip()
                candidate = candidate.strip('".').splitlines()[0] if candidate else ""
                if candidate and len(candidate) <= 60:
                    label = candidate
                    break
            except Exception as exc:
                print(f"  cluster {cluster['id']}: MGA error ({str(exc)[:60]})")

        cluster["name"] = label or fallback_name(cluster)
        flag = "" if label else "  [fallback]"
        print(f"  cluster {cluster['id']:>2} (n={cluster['size']:>4}): {cluster['name']}{flag}")

    deduplicate_names(clusters)


def deduplicate_names(clusters: list[dict[str, Any]]) -> None:
    """Two clusters can be handed the same label, which makes theme filtering
    ambiguous. Distinguish collisions with the cluster's top unused term."""
    seen: set[str] = set()
    for cluster in clusters:
        name = cluster["name"]
        if name.lower() not in seen:
            seen.add(name.lower())
            continue

        for term in cluster["terms"]:
            if term not in name.lower():
                name = f"{name} ({term.title()})"
                break
        else:
            name = f"{name} ({cluster['id']})"

        cluster["name"] = name
        seen.add(name.lower())
        print(f"  cluster {cluster['id']:>2} renamed to avoid a duplicate: {name}")


def build_themes(
    pairs: list[dict[str, str]], matrix: np.ndarray
) -> tuple[list[dict[str, Any]], np.ndarray]:
    labels, centroids = spherical_kmeans(matrix, NUM_CLUSTERS)
    cohesion = (matrix * centroids[labels]).sum(axis=1)

    clusters: list[dict[str, Any]] = []
    for cluster_id in range(NUM_CLUSTERS):
        member_idx = np.flatnonzero(labels == cluster_id)
        if not len(member_idx):
            continue
        members = [pairs[i] for i in member_idx]

        # Closest to the centroid reads best as a representative question.
        ranked = member_idx[np.argsort(-cohesion[member_idx])]
        examples = [pairs[i]["question"][:220] for i in ranked[:3]]

        codes: collections.Counter[str] = collections.Counter()
        for member in members:
            codes.update(extract_codes(member["question"] + " " + member["answer"]))

        lengths = [len(m["answer"]) for m in members]
        clusters.append(
            {
                "id": int(cluster_id),
                "size": int(len(member_idx)),
                "cohesion": round(float(cohesion[member_idx].mean()), 3),
                "terms": top_terms([m["document"] for m in members]),
                "examples": examples,
                "top_codes": [{"code": c, "count": n} for c, n in codes.most_common(5)],
                "median_answer_chars": int(np.median(lengths)),
                "p90_answer_chars": int(np.percentile(lengths, 90)),
            }
        )

    clusters.sort(key=lambda c: c["size"], reverse=True)
    return clusters, labels


def build_reuse(pairs: list[dict[str, str]], matrix: np.ndarray) -> dict[str, Any]:
    """Greedy grouping of near-identical questions at the cosine threshold."""
    order = np.argsort([-len(p["answer"]) for p in pairs])
    assigned = np.full(len(pairs), -1)
    groups: list[list[int]] = []

    for idx in order:
        if assigned[idx] != -1:
            continue
        sims = matrix @ matrix[idx]
        members = np.flatnonzero((sims >= NEAR_DUP_THRESHOLD) & (assigned == -1))
        assigned[members] = len(groups)
        groups.append([int(i) for i in members])

    repeated = [g for g in groups if len(g) > 1]
    repeated.sort(key=len, reverse=True)
    duplicated_pairs = sum(len(g) - 1 for g in repeated)

    def norm(text: str) -> str:
        return re.sub(r"\s+", " ", text.lower()).strip()

    verbatim_q = sum(
        c - 1 for c in collections.Counter(norm(p["question"]) for p in pairs).values() if c > 1
    )
    verbatim_a = sum(
        c - 1 for c in collections.Counter(norm(p["answer"]) for p in pairs).values() if c > 1
    )

    return {
        "threshold": NEAR_DUP_THRESHOLD,
        "group_count": len(repeated),
        "duplicated_pairs": int(duplicated_pairs),
        "duplicate_rate_pct": round(100 * duplicated_pairs / len(pairs), 1),
        "verbatim_repeat_questions": int(verbatim_q),
        "verbatim_repeat_answers": int(verbatim_a),
        "top_groups": [
            {
                "count": len(g),
                "question": pairs[g[0]]["question"][:260],
                "answer_preview": pairs[g[0]]["answer"][:260],
            }
            for g in repeated[:12]
        ],
    }


def code_pairs(codes: list[str]):
    """Distinct code pairs, skipping parent/child (P.8 with P.8.3 is not a pairing)."""
    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            a, b = codes[i], codes[j]
            if a.startswith(f"{b}.") or b.startswith(f"{a}."):
                continue
            yield a, b


def build_codes(pairs: list[dict[str, str]]) -> dict[str, Any]:
    counts: collections.Counter[str] = collections.Counter()
    co_occurrence: collections.Counter[tuple[str, str]] = collections.Counter()
    with_code = 0
    family: collections.Counter[str] = collections.Counter()

    for pair in pairs:
        codes = extract_codes(pair["question"] + " " + pair["answer"])
        if not codes:
            continue
        with_code += 1
        counts.update(codes)
        for code in codes:
            head = code.split(".")[0]
            family[
                "Drug substance (S)"
                if head == "S"
                else "Drug product (P)"
                if head == "P"
                else "Other"
            ] += 1
        for a, b in code_pairs(sorted(codes)):
            co_occurrence[(a, b)] += 1

    return {
        "coverage_pct": round(100 * with_code / len(pairs), 1),
        "distinct_codes": len(counts),
        "top": [{"code": c, "count": n} for c, n in counts.most_common(15)],
        "families": [{"family": f, "count": n} for f, n in family.most_common()],
        "co_occurrence": [
            {"a": a, "b": b, "count": n} for (a, b), n in co_occurrence.most_common(12)
        ],
    }


def build_effort(pairs: list[dict[str, str]]) -> dict[str, Any]:
    lengths = [len(p["answer"]) for p in pairs]
    source_docs = 0
    for pair in pairs:
        match = re.search(r"source:(.*)$", pair["answer"], re.IGNORECASE | re.DOTALL)
        if match:
            source_docs += len([s for s in re.split(r"[;\n]|\s{2,}", match.group(1)) if s.strip()])

    return {
        "median_answer_chars": int(np.median(lengths)),
        "p90_answer_chars": int(np.percentile(lengths, 90)),
        "max_answer_chars": int(max(lengths)),
        "answers_citing_sources": sum(
            1 for p in pairs if "source:" in p["answer"].lower()
        ),
        "avg_sources_per_citing_answer": round(
            source_docs / max(1, sum(1 for p in pairs if "source:" in p["answer"].lower())), 1
        ),
    }


def main() -> None:
    settings = get_settings()
    pairs = load_qna_pairs(settings.qna_path)
    print(f"Loaded {len(pairs)} usable Q&A pairs")

    collection = get_collection(settings)
    stored = collection.get(include=["embeddings", "documents"])
    if collection.count() != len(pairs):
        print(
            f"WARNING: Chroma holds {collection.count()} vectors but the JSON has "
            f"{len(pairs)} pairs. Re-run scripts/ingest.py."
        )

    # Chroma may return rows in any order; align them to the pair list by id.
    by_id = {doc_id: i for i, doc_id in enumerate(stored["ids"])}
    embeddings = np.array(stored["embeddings"], dtype=np.float32)
    ordered = np.stack([embeddings[by_id[p["id"]]] for p in pairs])
    ordered /= np.linalg.norm(ordered, axis=1, keepdims=True) + 1e-9
    print(f"Aligned {ordered.shape[0]} embeddings of dim {ordered.shape[1]}")

    print(f"Clustering into {NUM_CLUSTERS} themes...")
    clusters, labels = build_themes(pairs, ordered)

    print("Naming clusters via myGenAssist...")
    name_clusters(clusters)

    intents = collections.Counter(
        classify(p["question"], INTENT_RULES, "Comment / observation") for p in pairs
    )
    postures = collections.Counter(
        classify(p["answer"], POSTURE_RULES, "Other") for p in pairs
    )

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source_file": settings.qna_path.name,
        "total_pairs": len(pairs),
        "themes": clusters,
        "intents": [{"intent": k, "count": v} for k, v in intents.most_common()],
        "postures": [{"posture": k, "count": v} for k, v in postures.most_common()],
        "codes": build_codes(pairs),
        "reuse": build_reuse(pairs, ordered),
        "effort": build_effort(pairs),
        # Per-pair assignment so the chat assistant can filter by theme.
        "theme_by_id": {p["id"]: int(labels[i]) for i, p in enumerate(pairs)},
    }

    assert sum(c["size"] for c in clusters) == len(pairs), "cluster sizes must reconcile"
    assert sum(i["count"] for i in payload["intents"]) == len(pairs)
    assert sum(p["count"] for p in payload["postures"]) == len(pairs)

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size / 1024:.1f} KB)")
    print(f"  themes={len(clusters)} intents={len(payload['intents'])} "
          f"postures={len(payload['postures'])} "
          f"near-dup={payload['reuse']['duplicate_rate_pct']}%")


if __name__ == "__main__":
    main()
