# CMC Dashboard

Portfolio report for CMC response / RFI metrics, plus a **RAG chatbot** over `QnA_pairs_extracted.json`.

This folder is **independent of `yaap-infra-v2`**. Do not mix the two projects.

---

## What’s inside

| Path | Role |
|------|------|
| `index.html` | Dashboard UI + CMC Q&A chat panel (section 10) |
| `data.js` / `metrics.json` | Pre-aggregated metrics |
| `code_catalog.json` | CTD code titles |
| `QnA_pairs_extracted.json` | Knowledge base for RAG (~2k Q&A pairs) |
| `qna_insights.json` | Precomputed clustering/analytics for dashboard section 10 (generated) |
| `backend/` | FastAPI app, Chroma index, ingest/verify/insights scripts |
| `backend/app/corpus.py` | Exact (non-semantic) index for CTD-code and theme lookups |
| `backend/app/tools.py` | Tools the chat model can call, with their schemas |
| `backend/.env` | **Your secrets** — replace placeholders (gitignored) |
| `backend/.env.example` | Same key list, safe to commit |
| `Dockerfile` / `render.yaml` | Shareable hosting on Render |
| `_baseline/` | Pre–code-catalog rollback copies |

---

## Providers

| Stage | Default | Notes |
|-------|---------|-------|
| Embeddings / retrieval | **local** (`all-MiniLM-L6-v2` ONNX, 384-dim) | Runs on-device; no CMC text leaves the machine; no API key needed |
| Chat / generation | **myGenAssist** (`LLM_PROVIDER=mga`) | Enterprise-approved; public OpenAI is blocked |

Only the user's question plus the top retrieved snippets are sent to MGA for wording the answer.

## Secrets (`backend/.env`)

Edit [`backend/.env`](backend/.env) and replace placeholders:

| Key | Required | Purpose |
|-----|----------|---------|
| `MGA_API_KEY` | Yes | myGenAssist API key |
| `MGA_BASE_URL` | Yes | MGA OpenAI-compatible base URL (ask your MGA admin) |
| `MGA_MODEL` | Default `gpt-4o` | Model/deployment name exposed by MGA |
| `MGA_AUTH_HEADER` | Default `Authorization` | Use `api-key` or `Ocp-Apim-Subscription-Key` if the gateway requires it |
| `MGA_API_VERSION` | Blank | Set only if MGA is Azure-OpenAI style |
| `APP_PASSWORD` | Yes | Shared Basic Auth password for the shareable site |
| `APP_USERNAME` | Default `cmc` | Basic Auth username |
| `EMBED_PROVIDER` | Default `local` | `local` \| `mga` \| `openai` |

Then verify and index:

```bash
cd backend
.venv/bin/python scripts/verify_providers.py   # checks embeddings + MGA chat
.venv/bin/python scripts/ingest.py             # embeddings only; no key required
```

If MGA answers behave oddly, `scripts/diagnose_mga.py` probes system-prompt and long-context handling on the gateway.

The index is already built (2,048 pairs). Re-run `ingest.py` only when the Q&A JSON changes.

## Q&A Corpus Insights (section 10)

`qna_insights.json` powers the analytics section: semantic themes, question intent, most challenged CTD sections, response posture, repeat-question backlog, and answer effort. It also stores `theme_by_id`, the per-pair cluster assignment the chat assistant filters on.

```bash
cd backend
.venv/bin/python scripts/build_insights.py
```

The script reuses the embeddings already in Chroma (nothing is re-embedded) and clusters them with a numpy spherical k-means at `K=12`. Cluster names come from one short myGenAssist call each; if MGA is unreachable it falls back to top-term labels and still completes. Re-run it only when `QnA_pairs_extracted.json` changes, after `ingest.py`.

If the file is missing the dashboard still loads and section 10 shows a build hint instead.

Two helper checks:

```bash
node backend/scripts/check_dashboard.js   # element/chart wiring in index.html
node backend/scripts/check_insights.js    # JSON field contract and totals
```

---

## Run locally (dashboard + chatbot)

```bash
cd /Users/mohd.arif1/Documents/Healthcare_app/cmc-dashboard/backend
# 1) Fill backend/.env (MGA_API_KEY, MGA_BASE_URL, APP_PASSWORD)
# 2) Verify providers
.venv/bin/python scripts/verify_providers.py
# 3) Start API + static files
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) and sign in with `APP_USERNAME` / `APP_PASSWORD`.

Static-only mode (metrics only, **no chatbot**):

```bash
cd /Users/mohd.arif1/Documents/Healthcare_app/cmc-dashboard
python3 -m http.server 8765
```

---

## Architecture

```
Browser (index.html + chat)
        │  Basic Auth
        ▼
FastAPI (backend/app/main.py)
  ├─ serves static dashboard files
  ├─ /api/chat, /api/chat/stream → agent loop (max 4 rounds)
  │     MGA decides → search_qna (Chroma, semantic)
  │                 → list_questions (corpus.py, exact code/theme filter)
  │     → MGA answers from the tool output (+ accumulated sources)
  └─ /api/ingest → (re)build vector index from QnA JSON
```

- One Q&A pair = one Chroma document (`Question: …\nAnswer: …`).
- The model chooses its own tools, so it can count, list, and compare across the
  whole corpus instead of answering from one fixed top-5 retrieval.
- `list_questions` matches CTD codes with boundary-aware regex, so `S.4.1`
  returns 75 and does not absorb `S.4.10`. `3.2.S.4.1` resolves to the same key.
- When the corpus has no coverage the model answers from general CMC regulatory
  knowledge. The sources panel stays empty in that case, which is the provenance cue.
- Empty / `n.a.` pairs are dropped at ingest.

---

## Share with others (Render)

1. Create a [Render](https://render.com) account.
2. New Web Service from this folder (Docker) or use `render.yaml`.
3. Set env vars in Render: `MGA_API_KEY`, `MGA_BASE_URL`, `APP_PASSWORD`.
4. After first deploy, run ingest once (Render shell or one-off job):

```bash
python scripts/ingest.py
```

5. Share the Render URL; colleagues use Basic Auth credentials you set.

---

## Data policy

Indexing and retrieval are fully local — the 2,048 Q&A pairs are never uploaded. Only the **user question** and the **top retrieved snippets** go to myGenAssist for answer generation. Keep `APP_PASSWORD` private; treat the corpus as confidential regulatory IP.

Note: hosting this externally (Render) puts CMC content on a non-Bayer server. For internal-only sharing, run it on an internal host instead.

---

## API

| Endpoint | Auth | Notes |
|----------|------|-------|
| `GET /api/health` | No | Key configured? paths? |
| `POST /api/chat` | Basic | `{ "message": "..." }` → answer + sources |
| `POST /api/ingest` | Basic | Rebuild Chroma index |
| `GET /` | Basic | Dashboard |

---

## Updating metrics later

Same as before: refresh `metrics.json` / `data.js`, restart or refresh. Chatbot index is separate — re-run `scripts/ingest.py` only when `QnA_pairs_extracted.json` changes.
