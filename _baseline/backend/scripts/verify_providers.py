#!/usr/bin/env python3
"""Verify the configured chat provider (myGenAssist or OpenAI) and embeddings."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.rag import chat_client, get_embedding_function  # noqa: E402


def check_embeddings(settings) -> bool:
    print(f"\n[embeddings] provider={settings.embed_provider}")
    try:
        ef = get_embedding_function(settings)
        vec = ef(["cmc stability test"])
        print(f"OK: embedding dim={len(vec[0])}")
        return True
    except Exception as exc:
        print(f"FAIL: {exc}")
        return False


def check_chat(settings) -> bool:
    print(f"\n[chat] provider={settings.llm_provider} model={settings.chat_model()}")
    if not settings.chat_key_ready():
        key_name = "MGA_API_KEY" if settings.llm_provider == "mga" else "OPENAI_API_KEY"
        print(f"FAIL: {key_name} is missing or still a placeholder in backend/.env")
        return False
    if settings.llm_provider == "mga" and (
        not settings.mga_base_url or settings.mga_base_url.startswith("REPLACE_")
    ):
        print("FAIL: MGA_BASE_URL is missing or still a placeholder in backend/.env")
        print("      Ask your myGenAssist admin for the OpenAI-compatible API base URL.")
        return False

    try:
        client = chat_client(settings)
        # No max_tokens: a low cap makes this gateway return an empty message with
        # finish_reason=length, which would look like a failure.
        completion = client.chat.completions.create(
            model=settings.chat_model(),
            messages=[{"role": "user", "content": "Reply with exactly: ok"}],
        )
        reply = (completion.choices[0].message.content or "").strip()
        if not reply:
            print("FAIL: connected but the model returned empty content")
            return False
        print(f"OK: reply={reply!r}")
        return True
    except Exception as exc:
        print(f"FAIL: {exc}")
        print("      Check MGA_BASE_URL, MGA_MODEL, and MGA_AUTH_HEADER in backend/.env.")
        return False


def main() -> int:
    settings = get_settings()
    embed_ok = check_embeddings(settings)
    chat_ok = check_chat(settings)

    print("\n--- summary ---")
    print(f"embeddings: {'OK' if embed_ok else 'FAIL'}")
    print(f"chat:       {'OK' if chat_ok else 'FAIL'}")
    if embed_ok and not chat_ok:
        print("\nYou can still run scripts/ingest.py — indexing only needs embeddings.")
    return 0 if (embed_ok and chat_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
