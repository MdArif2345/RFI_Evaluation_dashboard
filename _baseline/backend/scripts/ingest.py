#!/usr/bin/env python3
"""Index QnA_pairs_extracted.json into Chroma. Requires OPENAI_API_KEY in backend/.env."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.rag import ingest  # noqa: E402


def main() -> None:
    settings = get_settings()
    print(f"Source: {settings.qna_path}")
    print(f"Chroma: {settings.chroma_dir}")
    result = ingest(settings, reset=True)
    print(f"Indexed {result['indexed']} Q&A pairs into '{result['collection']}'.")


if __name__ == "__main__":
    main()
