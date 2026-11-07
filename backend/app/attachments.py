"""Session-scoped chat uploads. Parsed text only; files are not written to disk."""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from threading import Lock
from typing import Any

MAX_FILES = 3
MAX_BYTES = 10 * 1024 * 1024
CONTEXT_CHARS = 12_000
ALLOWED_EXT = {".docx", ".pdf", ".txt", ".csv"}

_store: dict[str, list[dict[str, Any]]] = {}
_lock = Lock()


def normalize_ext(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def parse_upload(filename: str, data: bytes) -> str:
    ext = normalize_ext(filename)
    if ext not in ALLOWED_EXT:
        raise ValueError(f"Unsupported file type: {ext or 'unknown'}")
    if ext == ".docx":
        from docx import Document

        doc = Document(io.BytesIO(data))
        parts = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts)
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
        return "\n\n".join(p for p in pages if p)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _public(item: dict[str, Any]) -> dict[str, Any]:
    text = item.get("text") or ""
    preview = " ".join(text.split())
    return {
        "id": item["id"],
        "filename": item["filename"],
        "chars": len(text),
        "preview": preview[:220],
        "low_text": len(text.strip()) < 40,
    }


def list_attachments(session_id: str) -> list[dict[str, Any]]:
    with _lock:
        return [_public(x) for x in _store.get(session_id, [])]


def get_attachment(session_id: str, attach_id: str) -> dict[str, Any] | None:
    with _lock:
        for item in _store.get(session_id, []):
            if item["id"] == attach_id:
                return item
    return None


def clear_attachments(session_id: str) -> None:
    with _lock:
        _store.pop(session_id, None)


def remove_attachment(session_id: str, attach_id: str) -> bool:
    with _lock:
        items = _store.get(session_id, [])
        kept = [x for x in items if x["id"] != attach_id]
        if len(kept) == len(items):
            return False
        if kept:
            _store[session_id] = kept
        else:
            _store.pop(session_id, None)
        return True


def add_attachment(session_id: str, filename: str, data: bytes) -> dict[str, Any]:
    name = Path(filename or "upload").name
    ext = normalize_ext(name)
    if ext not in ALLOWED_EXT:
        raise ValueError(f"Unsupported file type: {ext or 'unknown'}")
    if len(data) > MAX_BYTES:
        raise ValueError("File is larger than 10 MB")
    text = parse_upload(name, data)
    item = {
        "id": uuid.uuid4().hex[:12],
        "filename": name,
        "text": text,
    }
    with _lock:
        current = list(_store.get(session_id, []))
        if len(current) >= MAX_FILES:
            raise ValueError(f"At most {MAX_FILES} files per chat")
        current.append(item)
        _store[session_id] = current
    return _public(item)


def context_block(session_id: str | None) -> str:
    if not session_id:
        return ""
    with _lock:
        items = list(_store.get(session_id, []))
    if not items:
        return ""
    parts = [
        "The user uploaded the following document(s) for this chat only. "
        "Use them when the question refers to this file, this letter, or the upload. "
        "Do not treat them as part of the shared CMC corpus unless asked to compare."
    ]
    for item in items:
        text = (item.get("text") or "")[:CONTEXT_CHARS]
        name = item.get("filename") or "document"
        parts.append(f"--- Uploaded file: {name} ---\n{text}")
    return "\n\n".join(parts)
