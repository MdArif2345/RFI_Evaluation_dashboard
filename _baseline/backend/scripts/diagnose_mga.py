#!/usr/bin/env python3
"""Diagnose how the MGA gateway handles system prompts and long context."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.rag import chat_client, retrieve  # noqa: E402

QUESTION = "What leachables data was provided for the drug product?"


def ask(client, model, messages, label):
    try:
        r = client.chat.completions.create(model=model, messages=messages, temperature=0.2)
        msg = r.choices[0].message
        content = (msg.content or "").strip()
        print(f"\n[{label}] finish={r.choices[0].finish_reason} len={len(content)}")
        print(f"  -> {content[:300] or '(EMPTY)'}")
    except Exception as exc:
        print(f"\n[{label}] ERROR: {exc}")


def main() -> None:
    s = get_settings()
    client = chat_client(s)
    model = s.chat_model()

    hits = retrieve(QUESTION, s)
    top = hits[0]["document"]
    full = "\n\n---\n\n".join(h["document"] for h in hits)

    ask(client, model, [{"role": "user", "content": "Say hello in 3 words."}], "1 plain user")

    ask(
        client,
        model,
        [
            {"role": "system", "content": "You are a terse assistant. Always start replies with 'SYS-OK'."},
            {"role": "user", "content": "Say hello."},
        ],
        "2 system role honored?",
    )

    ask(
        client,
        model,
        [{"role": "user", "content": f"Context:\n{top}\n\nQuestion: {QUESTION}\nAnswer from the context."}],
        "3 single source in user msg",
    )

    ask(
        client,
        model,
        [{"role": "user", "content": f"Context:\n{full}\n\nQuestion: {QUESTION}\nAnswer from the context."}],
        f"4 all sources ({len(full)} chars)",
    )


if __name__ == "__main__":
    main()
