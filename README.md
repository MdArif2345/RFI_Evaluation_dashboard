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
| `backend/` | FastAPI app, Chroma index, ingest/verify/diagnose scripts |
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
  ├─ /api/chat  → Chroma retrieve (local) → MGA generate (+ sources)
  └─ /api/ingest → (re)build vector index from QnA JSON
```

- One Q&A pair = one Chroma document (`Question: …\nAnswer: …`).
- Answers are restricted to retrieved context; otherwise the bot says it has no match.
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
