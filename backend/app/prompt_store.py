"""Versioned, team-editable chat system prompt.

Versions live in the `prompt_versions` table of the chat SQLite DB. Exactly one
row is active; saving or restoring switches the active row. Version 1 is the
built-in default, seeded on first start.
"""

from __future__ import annotations

import string
from threading import Lock
from typing import Any

from .chat_db import _db, _now

ALLOWED_PLACEHOLDERS = (
    "total",
    "total_documents",
    "sm_count",
    "lm_count",
    "unique_products",
    "unique_countries",
    "top_products_str",
    "top_countries_str",
    "catalog_size",
    "themes",
)
MAX_PROMPT_CHARS = 60_000
MAX_AUTHOR_CHARS = 80
MAX_NOTE_CHARS = 300

_lock = Lock()


class PromptValidationError(ValueError):
    pass


def validate_template(content: str) -> None:
    """Raise PromptValidationError unless `content` renders with the known placeholders."""
    if not content.strip():
        raise PromptValidationError("The prompt cannot be empty.")
    if len(content) > MAX_PROMPT_CHARS:
        raise PromptValidationError(
            f"The prompt is {len(content):,} characters; the limit is {MAX_PROMPT_CHARS:,}."
        )
    try:
        fields = [f for _, f, _, _ in string.Formatter().parse(content) if f is not None]
    except ValueError as exc:
        raise PromptValidationError(
            f"Unbalanced braces ({exc}). Write a literal brace as {{{{ or }}}}."
        ) from exc
    unknown = sorted({f for f in fields if f not in ALLOWED_PLACEHOLDERS})
    if unknown:
        shown = ", ".join("{" + f + "}" for f in unknown)
        raise PromptValidationError(
            f"Unknown placeholder(s): {shown}. Allowed: "
            + ", ".join("{" + p + "}" for p in ALLOWED_PLACEHOLDERS)
            + ". Write a literal brace as {{ or }}."
        )
    try:
        content.format(**{p: "0" for p in ALLOWED_PLACEHOLDERS})
    except (ValueError, KeyError, IndexError) as exc:
        raise PromptValidationError(f"The prompt does not render: {exc}") from exc


def seed_default(default_content: str) -> None:
    """Insert the built-in prompt as version 1 if the table is empty."""
    with _lock:
        db = _db()
        if db.execute("SELECT 1 FROM prompt_versions LIMIT 1").fetchone():
            return
        db.execute(
            "INSERT INTO prompt_versions (content, author, note, created_at, is_active) "
            "VALUES (?, 'default', 'Built-in default prompt', ?, 1)",
            (default_content, _now()),
        )
        db.commit()


def _meta(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "author": row["author"],
        "note": row["note"],
        "created_at": row["created_at"],
        "is_active": bool(row["is_active"]),
        "chars": row["chars"],
    }


def get_active() -> dict[str, Any] | None:
    row = _db().execute(
        "SELECT id, content, author, note, created_at, is_active, LENGTH(content) AS chars "
        "FROM prompt_versions WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return {**_meta(row), "content": row["content"]}


def list_versions(limit: int = 100) -> list[dict[str, Any]]:
    rows = _db().execute(
        "SELECT id, author, note, created_at, is_active, LENGTH(content) AS chars "
        "FROM prompt_versions ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_meta(r) for r in rows]


def get_version(version_id: int) -> dict[str, Any] | None:
    row = _db().execute(
        "SELECT id, content, author, note, created_at, is_active, LENGTH(content) AS chars "
        "FROM prompt_versions WHERE id = ?",
        (version_id,),
    ).fetchone()
    if not row:
        return None
    return {**_meta(row), "content": row["content"]}


def save_version(content: str, author: str, note: str) -> dict[str, Any]:
    """Validate, store as a new version, and make it active."""
    validate_template(content)
    author = author.strip()[:MAX_AUTHOR_CHARS]
    note = note.strip()[:MAX_NOTE_CHARS]
    if not author:
        raise PromptValidationError("Enter your name.")
    if not note:
        raise PromptValidationError("Enter a short change note.")
    with _lock:
        db = _db()
        db.execute("UPDATE prompt_versions SET is_active = 0 WHERE is_active = 1")
        cur = db.execute(
            "INSERT INTO prompt_versions (content, author, note, created_at, is_active) "
            "VALUES (?, ?, ?, ?, 1)",
            (content, author, note, _now()),
        )
        db.commit()
        new_id = cur.lastrowid
    return get_version(int(new_id))  # type: ignore[return-value]


def activate(version_id: int) -> dict[str, Any] | None:
    """Make an existing version active. Returns None if it does not exist."""
    with _lock:
        db = _db()
        if not db.execute("SELECT 1 FROM prompt_versions WHERE id = ?", (version_id,)).fetchone():
            return None
        db.execute("UPDATE prompt_versions SET is_active = 0 WHERE is_active = 1")
        db.execute("UPDATE prompt_versions SET is_active = 1 WHERE id = ?", (version_id,))
        db.commit()
    return get_version(version_id)
