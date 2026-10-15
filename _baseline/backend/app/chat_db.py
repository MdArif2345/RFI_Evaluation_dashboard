"""SQLite-backed server-side chat history.

Stores sessions and messages so conversations persist across browsers, devices,
and page refreshes. The DB file is created at startup and gitignored.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_conn: sqlite3.Connection | None = None


def init_db(db_path: Path) -> None:
    """Create tables if needed and cache the connection."""
    global _conn
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(db_path), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            username    TEXT NOT NULL,
            title       TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL REFERENCES sessions(id),
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            sources     TEXT,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_username ON sessions(username);
    """)
    _conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("chat_db.init_db() has not been called")
    return _conn


def ensure_session(session_id: str, username: str) -> None:
    """Create the session row if it does not already exist."""
    db = _db()
    existing = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if existing:
        return
    now = _now()
    db.execute(
        "INSERT INTO sessions (id, username, title, created_at, updated_at) VALUES (?, ?, NULL, ?, ?)",
        (session_id, username, now, now),
    )
    db.commit()


def save_message(
    session_id: str,
    role: str,
    content: str,
    sources: list[dict[str, Any]] | None = None,
) -> None:
    """Persist one message and update the session timestamp (and title if first user msg)."""
    db = _db()
    now = _now()
    sources_json = json.dumps(sources, ensure_ascii=False) if sources else None
    db.execute(
        "INSERT INTO messages (session_id, role, content, sources, created_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, role, content, sources_json, now),
    )
    db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))

    # Auto-set title from the first user message.
    if role == "user":
        row = db.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row and not row["title"]:
            title = content[:60].strip()
            if len(content) > 60:
                title += "…"
            db.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))

    db.commit()


def delete_session(session_id: str) -> bool:
    """Delete a session and all its messages. Returns True if the session existed."""
    db = _db()
    row = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return False
    db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    db.commit()
    return True


def get_sessions(username: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return recent sessions for a user, newest first."""
    db = _db()
    rows = db.execute(
        "SELECT id, username, title, created_at, updated_at FROM sessions "
        "WHERE username = ? ORDER BY updated_at DESC LIMIT ?",
        (username, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_session_messages(session_id: str) -> list[dict[str, Any]]:
    """Return all messages for a session, in chronological order."""
    db = _db()
    rows = db.execute(
        "SELECT id, session_id, role, content, sources, created_at FROM messages "
        "WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    ).fetchall()
    result = []
    for r in rows:
        msg = dict(r)
        if msg["sources"]:
            try:
                msg["sources"] = json.loads(msg["sources"])
            except json.JSONDecodeError:
                msg["sources"] = []
        else:
            msg["sources"] = []
        result.append(msg)
    return result


def get_analytics() -> dict[str, Any]:
    """Basic usage stats: totals, active users, top questions, daily volume."""
    db = _db()

    total_sessions = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    total_messages = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    active_users = [
        {"username": r[0], "sessions": r[1]}
        for r in db.execute(
            "SELECT username, COUNT(*) as cnt FROM sessions GROUP BY username ORDER BY cnt DESC LIMIT 20"
        ).fetchall()
    ]

    top_questions = [
        {"question": r[0], "count": r[1]}
        for r in db.execute(
            "SELECT content, COUNT(*) as cnt FROM messages WHERE role = 'user' "
            "GROUP BY content ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
    ]

    daily_volume = [
        {"date": r[0], "messages": r[1]}
        for r in db.execute(
            "SELECT DATE(created_at) as d, COUNT(*) FROM messages "
            "WHERE created_at >= DATE('now', '-30 days') "
            "GROUP BY d ORDER BY d"
        ).fetchall()
    ]

    return {
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "active_users": active_users,
        "top_questions": top_questions,
        "daily_volume": daily_volume,
    }
