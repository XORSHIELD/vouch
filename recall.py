"""
Vouch — case-linked chat memory.

Turns stored per case, so a conversation survives across sessions:
what the user told the agent yesterday is still on the table today.
Memory attaches only to a real case id; anonymous small talk stays
ephemeral, and the server only passes case ids the caller may access.

SQLite, in the same database as the cases themselves.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime

import cases

MAX_TURNS = 50
MAX_TEXT = 2000
MAX_CONTEXT_CHARS = 4000


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(cases.DB_PATH), exist_ok=True)
    con = sqlite3.connect(cases.DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init() -> None:
    con = _connect()
    try:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_turns_case ON chat_turns(case_id);
            """
        )
        con.commit()
    finally:
        con.close()


def attached(case_id: str) -> bool:
    """A memory-owning case id: non-empty and attached to a real case."""
    cid = (case_id or "").strip()
    if not cid:
        return False
    init()
    return cases.get_case(cid) is not None


def remember(case_id: str, role: str, text: str) -> None:
    """Store one turn. Unknown cases and empty text store nothing."""
    if role not in ("user", "assistant"):
        return
    text = (text or "").strip()[:MAX_TEXT]
    if not text or not attached(case_id):
        return
    cid = case_id.strip()
    now = datetime.now().isoformat(timespec="seconds")
    con = _connect()
    try:
        con.execute(
            "INSERT INTO chat_turns (case_id, role, text, at)"
            " VALUES (?,?,?,?)", (cid, role, text, now))
        con.execute(
            """DELETE FROM chat_turns WHERE case_id = ? AND id NOT IN
               (SELECT id FROM chat_turns WHERE case_id = ?
                ORDER BY id DESC LIMIT ?)""",
            (cid, cid, MAX_TURNS))
        con.commit()
    finally:
        con.close()


def context(case_id: str) -> list[dict]:
    """Recalled turns, oldest first, as UI-style history entries."""
    if not (case_id or "").strip():
        return []
    init()
    con = _connect()
    try:
        rows = con.execute(
            "SELECT role, text FROM chat_turns WHERE case_id = ?"
            " ORDER BY id ASC LIMIT ?", (case_id.strip(), MAX_TURNS),
        ).fetchall()
    finally:
        con.close()
    out = [{"role": r["role"], "text": r["text"]} for r in rows]
    total = sum(len(t["text"]) for t in out)
    while total > MAX_CONTEXT_CHARS and len(out) > 2:
        total -= len(out.pop(0)["text"])
    return out


def forget(case_id: str) -> None:
    """Drop every stored turn for a case."""
    if not (case_id or "").strip():
        return
    init()
    con = _connect()
    try:
        con.execute("DELETE FROM chat_turns WHERE case_id = ?",
                    (case_id.strip(),))
        con.commit()
    finally:
        con.close()
