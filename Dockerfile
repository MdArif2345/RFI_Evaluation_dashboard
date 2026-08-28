# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
  && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Dashboard static files + QnA + backend code
COPY QnA_pairs_extracted.json /app/QnA_pairs_extracted.json
COPY index.html data.js metrics.json code_catalog.json /app/
COPY backend /app/backend

ENV PYTHONUNBUFFERED=1
ENV QNA_PATH=/app/QnA_pairs_extracted.json
ENV CHROMA_DIR=/app/backend/chroma_data

# ============================================================
# IMPORTANT: Build the ChromaDB vector index during Docker build
# This embeds all Q&A pairs so the RAG chatbot can retrieve them
# ============================================================
WORKDIR /app/backend
RUN python -m scripts.ingest

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
