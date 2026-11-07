"""Uploaded TRD writing profiles, mapped to the TRD/CTD codes they govern.

Profiles live on SharePoint; the team exports them and uploads DOCX/PDF/TXT
here. Only the parsed text is stored. A profile mapped to `P.5.1` governs
`P.5.1.01`, `P.5.1.02`, ... unless a profile for the exact code exists.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from threading import Lock
from typing import Any

from .attachments import normalize_ext, parse_upload
from .chat_db import _db, _now
from .corpus import CODE_TOKEN, MODULE_PREFIX

ALLOWED_EXT = {".docx", ".pdf", ".txt"}
MAX_BYTES = 15 * 1024 * 1024
MAX_CODES = 40
CODE_SHAPE = re.compile(r"^[A-Z](?:\.\d+)+$")

_lock = Lock()


class ProfileError(ValueError):
    pass


def clean_code(raw: str) -> str:
    return MODULE_PREFIX.sub("", raw.strip().upper()).strip(".")


def parse_codes(raw: str) -> list[str]:
    """Split a user-entered code list and reject anything that is not a code."""
    out: list[str] = []
    for token in re.split(r"[\s,;]+", raw or ""):
        if not token:
            continue
        code = clean_code(token)
        if not CODE_SHAPE.match(code):
            raise ProfileError(f"'{token}' is not a TRD/CTD code (expected e.g. P.5.1 or P.5.1.01).")
        if code not in out:
            out.append(code)
    if len(out) > MAX_CODES:
        raise ProfileError(f"At most {MAX_CODES} codes per profile.")
    return out


def _codes_in(source: str) -> list[str]:
    found: list[str] = []
    for token in CODE_TOKEN.findall(source):
        code = clean_code(token)
        if CODE_SHAPE.match(code) and code not in found:
            found.append(code)
    return found


def suggest_codes(filename: str, text: str) -> list[str]:
    """Codes named in the filename; otherwise only the first code in the text.

    Profile bodies cross-reference other sections (e.g. "see P.5.6"), so taking
    every code from the text would make this profile govern unrelated codes.
    """
    from_name = _codes_in(Path(filename).stem.replace("_", " "))
    if from_name:
        return from_name[:MAX_CODES]
    return _codes_in(text[:3000])[:1]


def _meta(row: Any) -> dict[str, Any]:
    content = row["content"]
    return {
        "id": row["id"],
        "codes": json.loads(row["codes"]),
        "filename": row["filename"],
        "chars": len(content),
        "preview": " ".join(content.split())[:200],
        "uploaded_by": row["uploaded_by"],
        "uploaded_at": row["uploaded_at"],
    }


def list_profiles() -> list[dict[str, Any]]:
    rows = _db().execute(
        "SELECT id, codes, filename, content, uploaded_by, uploaded_at FROM trd_profiles ORDER BY id DESC"
    ).fetchall()
    return [_meta(r) for r in rows]


def add_profile(filename: str, data: bytes, codes_raw: str, uploaded_by: str) -> dict[str, Any]:
    name = Path(filename or "profile").name
    if normalize_ext(name) not in ALLOWED_EXT:
        raise ProfileError("Upload a DOCX, PDF or TXT export of the TRD profile.")
    if len(data) > MAX_BYTES:
        raise ProfileError("File is larger than 15 MB.")
    author = (uploaded_by or "").strip()[:80] or "unknown"
    try:
        text = parse_upload(name, data).strip()
    except Exception as exc:
        raise ProfileError(f"Could not read {name}: {exc}") from exc
    if len(text) < 40:
        raise ProfileError(f"{name} has almost no readable text (scanned PDF?).")
    codes = parse_codes(codes_raw) or suggest_codes(name, text)
    if not codes:
        raise ProfileError("Enter the TRD codes this profile covers (e.g. P.5.1 or P.5.1.01).")
    with _lock:
        db = _db()
        cur = db.execute(
            "INSERT INTO trd_profiles (codes, filename, content, uploaded_by, uploaded_at) VALUES (?, ?, ?, ?, ?)",
            (json.dumps(codes), name, text, author, _now()),
        )
        db.commit()
        new_id = cur.lastrowid
    row = _db().execute("SELECT * FROM trd_profiles WHERE id = ?", (new_id,)).fetchone()
    return _meta(row)


def update_codes(profile_id: int, codes_raw: str) -> dict[str, Any] | None:
    codes = parse_codes(codes_raw)
    if not codes:
        raise ProfileError("A profile needs at least one code.")
    with _lock:
        db = _db()
        cur = db.execute("UPDATE trd_profiles SET codes = ? WHERE id = ?", (json.dumps(codes), profile_id))
        db.commit()
        if not cur.rowcount:
            return None
    row = _db().execute("SELECT * FROM trd_profiles WHERE id = ?", (profile_id,)).fetchone()
    return _meta(row)


def delete_profile(profile_id: int) -> bool:
    with _lock:
        db = _db()
        cur = db.execute("DELETE FROM trd_profiles WHERE id = ?", (profile_id,))
        db.commit()
        return bool(cur.rowcount)


def _all_with_content() -> list[dict[str, Any]]:
    rows = _db().execute("SELECT id, codes, filename, content FROM trd_profiles ORDER BY id DESC").fetchall()
    return [
        {"id": r["id"], "codes": json.loads(r["codes"]), "filename": r["filename"], "content": r["content"]}
        for r in rows
    ]


def _matches(profile_code: str, code: str) -> bool:
    return code == profile_code or code.startswith(profile_code + ".")


def profile_for_code(code: str, profiles: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Most specific profile whose mapped code equals or is a parent of `code`.

    Ties go to the newest upload. Returns the profile plus `matched_code`
    and a content hash used for caching.
    """
    best: tuple[int, int, dict[str, Any], str] | None = None
    for p in profiles if profiles is not None else _all_with_content():
        for pc in p["codes"]:
            if _matches(pc, code):
                key = (len(pc.split(".")), p["id"])
                if best is None or key > best[:2]:
                    best = (key[0], key[1], p, pc)
    if best is None:
        return None
    p, pc = best[2], best[3]
    return {
        **p,
        "matched_code": pc,
        "hash": hashlib.sha256(p["content"].encode("utf-8")).hexdigest()[:16],
    }


def coverage(codes: list[str]) -> dict[str, Any]:
    profiles = _all_with_content()
    covered, missing = [], []
    for code in codes:
        match = profile_for_code(code, profiles)
        if match:
            covered.append({"code": code, "profile_id": match["id"], "filename": match["filename"], "matched_code": match["matched_code"]})
        else:
            missing.append(code)
    return {"covered": covered, "missing": missing}
