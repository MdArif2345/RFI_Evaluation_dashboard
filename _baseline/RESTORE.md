# Restore baseline (pre–QnA Extractor section)

Snapshot of the dashboard **before** integrating the native QnA Extractor / corpus-append feature.

Taken from the working Portfolio + Yearly Overview + TRD chatbot/recommendations state.

## Restore UI + metrics artifacts

From `cmc-dashboard/`:

```bash
cp _baseline/index.html ./index.html
cp _baseline/data.js ./data.js
cp _baseline/metrics.json ./metrics.json
cp _baseline/corpus_stats.json ./corpus_stats.json
cp _baseline/yearly_overview.json ./yearly_overview.json
cp _baseline/qna_insights.json ./qna_insights.json
cp _baseline/code_catalog.json ./code_catalog.json
cp _baseline/Dockerfile ./Dockerfile
cp _baseline/README.md ./README.md
cp _baseline/start_server.sh ./start_server.sh
```

## Restore backend app + rebuild scripts

```bash
rsync -a --delete _baseline/backend/app/ backend/app/
rsync -a --delete _baseline/backend/scripts/ backend/scripts/
cp _baseline/backend/requirements.txt backend/requirements.txt
```

Restart the server after restoring backend files:

```bash
./start_server.sh
```

## Not included in baseline

- `cmc_response_documents (4).json` (large source corpus — keep the live file)
- `backend/.env` / secrets
- `backend/chroma_data/` (re-run ingest if needed: `POST /api/ingest` or `python -m scripts.ingest`)
- `CMC Data Analysis_v6/` (standalone extractor reference)
- `venv/` / `.git`

## Optional: re-ingest vectors after backend restore

```bash
cd backend && python -m scripts.ingest
```
