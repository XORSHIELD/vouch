"""
Vouch — case store.

Every assessment can become a case: a record of what was found, the documents
generated, the deadlines, and what happened. This is the part that turns a
one-off check into follow-through.

SQLite, because a demo must survive being run on a laptop with no network and
no database server.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any

from actions import Document, escalation_plan
from engine import Assessment, PackInput

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".data")
DB_PATH = os.path.join(DATA_DIR, "vouch.sqlite")


def _connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init() -> None:
    con = _connect()
    try:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                band TEXT NOT NULL,
                score REAL NOT NULL,
                headline TEXT,
                nafdac TEXT,
                product TEXT,
                manufacturer TEXT,
                batch TEXT,
                seller TEXT,
                location TEXT,
                price TEXT,
                exp_date TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                outcome TEXT,
                next_action_at TEXT,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                at TEXT NOT NULL,
                kind TEXT NOT NULL,
                note TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cases_batch ON cases(batch);
            CREATE INDEX IF NOT EXISTS idx_cases_nafdac ON cases(nafdac);
            CREATE INDEX IF NOT EXISTS idx_docs_case ON documents(case_id);
            CREATE INDEX IF NOT EXISTS idx_events_case ON events(case_id);
            """
        )
        con.commit()
    finally:
        con.close()


def new_id() -> str:
    return "VC-" + uuid.uuid4().hex[:6].upper()


def create_case(pack: PackInput, assessment: Assessment,
                opened_on: date | None = None) -> dict:
    """Open a case. `opened_on` lets a rehearsal create history that is already
    aged, so the follow-up screen can be shown without waiting a week."""
    init()
    cid = new_id()
    opened = opened_on or date.today()
    now = datetime.combine(opened, datetime.min.time()).replace(hour=9).isoformat(
        timespec="seconds")
    steps = escalation_plan(assessment, opened)
    upcoming = [s for s in steps if s.day > 0]
    next_action = upcoming[0].due if upcoming else None

    con = _connect()
    try:
        con.execute(
            """INSERT INTO cases (id, created_at, band, score, headline, nafdac,
                   product, manufacturer, batch, seller, location, price,
                   exp_date, status, next_action_at, payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cid, now, assessment.band, assessment.score, assessment.headline,
                pack.nafdac_number, pack.product_name, pack.manufacturer,
                pack.batch, pack.seller, pack.location, pack.price, pack.exp_date,
                "open", next_action, json.dumps(assessment.to_dict(), default=str),
            ),
        )
        con.execute(
            "INSERT INTO events (case_id, at, kind, note) VALUES (?,?,?,?)",
            (cid, now, "opened", assessment.headline),
        )
        con.commit()
    finally:
        con.close()
    return get_case(cid)


def attach_document(case_id: str, doc: Document) -> None:
    init()
    con = _connect()
    try:
        con.execute(
            "INSERT INTO documents (case_id, kind, title, body, created_at) "
            "VALUES (?,?,?,?,?)",
            (case_id, doc.kind, doc.title, doc.body,
             datetime.now().isoformat(timespec="seconds")),
        )
        con.execute(
            "INSERT INTO events (case_id, at, kind, note) VALUES (?,?,?,?)",
            (case_id, datetime.now().isoformat(timespec="seconds"),
             "document", doc.title),
        )
        con.commit()
    finally:
        con.close()


def add_event(case_id: str, kind: str, note: str = "") -> None:
    init()
    con = _connect()
    try:
        con.execute(
            "INSERT INTO events (case_id, at, kind, note) VALUES (?,?,?,?)",
            (case_id, datetime.now().isoformat(timespec="seconds"), kind, note),
        )
        con.commit()
    finally:
        con.close()


def get_case(case_id: str) -> dict | None:
    init()
    con = _connect()
    try:
        row = con.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            return None
        docs = con.execute(
            "SELECT kind, title, created_at FROM documents WHERE case_id = ? "
            "ORDER BY id", (case_id,)
        ).fetchall()
        events = con.execute(
            "SELECT at, kind, note FROM events WHERE case_id = ? ORDER BY id",
            (case_id,)
        ).fetchall()
    finally:
        con.close()

    case = dict(row)
    try:
        case["assessment"] = json.loads(case.pop("payload"))
    except Exception:
        case["assessment"] = None
    case["documents"] = [dict(d) for d in docs]
    case["events"] = [dict(e) for e in events]
    case["steps"] = _steps_for(case)
    case["current"] = _current_step(case["steps"])
    case["overdue"] = bool(
        case["status"] == "open"
        and case.get("next_action_at")
        and case["next_action_at"] < date.today().isoformat()
    )
    return case


def _assessment_stub(case: dict) -> Any:
    """escalation_plan only needs the band; rebuild a minimal object."""
    class Stub:
        band = case.get("band", "review")
        checks: list = []
    return Stub()


def _steps_for(case: dict) -> list[dict]:
    """The escalation schedule with each step's completion state attached."""
    done = {e["kind"] for e in case.get("events", [])
            if str(e.get("kind", "")).startswith("step:")}
    steps = escalation_plan(
        _assessment_stub(case), date.fromisoformat(case["created_at"][:10]))
    today = date.today().isoformat()
    out = []
    for s in steps:
        d = asdict(s)
        d["done"] = f"step:{s.day}" in done
        d["overdue"] = (not d["done"]) and s.day > 0 and s.due < today
        d["due_today"] = (not d["done"]) and s.day > 0 and s.due == today
        out.append(d)
    return out


def _current_step(steps: list[dict]) -> dict | None:
    for s in steps:
        if not s["done"] and s["day"] > 0:
            return s
    return None


def _recompute_next(case_id: str) -> None:
    """Point next_action_at at the first unfinished step. Null when all done."""
    init()
    con = _connect()
    try:
        row = con.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            return
        events = con.execute(
            "SELECT at, kind, note FROM events WHERE case_id = ? ORDER BY id",
            (case_id,),
        ).fetchall()
        case = dict(row)
        case["events"] = [dict(e) for e in events]
        current = _current_step(_steps_for(case))
        con.execute(
            "UPDATE cases SET next_action_at = ? WHERE id = ?",
            (current["due"] if current else None, case_id),
        )
        con.commit()
    finally:
        con.close()


def mark_step(case_id: str, day: int, note: str = "") -> dict | None:
    """Record that a scheduled step was actually done, and advance the clock."""
    add_event(case_id, f"step:{day}", note or "done")
    _recompute_next(case_id)
    return get_case(case_id)


def snooze(case_id: str, days: int = 3) -> dict | None:
    """Push the next action out, without pretending it was done."""
    if days < 1:
        days = 1
    target = (date.today() + timedelta(days=days)).isoformat()
    init()
    con = _connect()
    try:
        con.execute(
            "UPDATE cases SET next_action_at = ? WHERE id = ? AND status = 'open'",
            (target, case_id),
        )
        con.execute(
            "INSERT INTO events (case_id, at, kind, note) VALUES (?,?,?,?)",
            (case_id, datetime.now().isoformat(timespec="seconds"), "snoozed",
             f"pushed to {target}"),
        )
        con.commit()
    finally:
        con.close()
    return get_case(case_id)


def queue(limit: int = 50) -> list[dict]:
    """What a user actually needs on opening the app: what is due now."""
    today = date.today().isoformat()
    out = []
    for c in list_cases(limit=limit, status="open"):
        full = get_case(c["id"])
        if not full:
            continue
        cur = full.get("current")
        out.append({
            "id": full["id"],
            "band": full["band"],
            "headline": full["headline"],
            "product": full["product"],
            "nafdac": full["nafdac"],
            "batch": full["batch"],
            "next_action_at": full.get("next_action_at"),
            "overdue": full.get("overdue", False),
            "due_today": (full.get("next_action_at") == today),
            "step": cur,
            "steps_left": sum(1 for s in full["steps"]
                              if not s["done"] and s["day"] > 0),
        })
    out.sort(key=lambda x: (
        0 if x["overdue"] else 1 if x["due_today"] else 2,
        x["next_action_at"] or "9999",
    ))
    return out



def list_cases(limit: int = 50, status: str | None = None) -> list[dict]:
    init()
    con = _connect()
    try:
        if status:
            rows = con.execute(
                "SELECT id, created_at, band, score, headline, nafdac, product, "
                "batch, seller, status, next_action_at FROM cases "
                "WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id, created_at, band, score, headline, nafdac, product, "
                "batch, seller, status, next_action_at FROM cases "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        con.close()
    today = date.today().isoformat()
    out = []
    for r in rows:
        d = dict(r)
        d["overdue"] = bool(
            d["status"] == "open" and d.get("next_action_at")
            and d["next_action_at"] < today
        )
        out.append(d)
    return out


def close_case(case_id: str, outcome: str, status: str = "closed") -> dict | None:
    init()
    con = _connect()
    try:
        con.execute(
            "UPDATE cases SET status = ?, outcome = ?, next_action_at = NULL "
            "WHERE id = ?", (status, outcome, case_id),
        )
        con.execute(
            "INSERT INTO events (case_id, at, kind, note) VALUES (?,?,?,?)",
            (case_id, datetime.now().isoformat(timespec="seconds"),
             "closed", outcome),
        )
        con.commit()
    finally:
        con.close()
    return get_case(case_id)


# --------------------------------------------------------------------------
# Network signals: batches and clusters
# --------------------------------------------------------------------------

def seen_batches_for(nafdac_number: str) -> list[str]:
    if not nafdac_number:
        return []
    init()
    con = _connect()
    try:
        rows = con.execute(
            "SELECT DISTINCT batch FROM cases WHERE nafdac = ? AND batch != ''",
            (nafdac_number,),
        ).fetchall()
    finally:
        con.close()
    return [r["batch"] for r in rows]


def cluster(nafdac_number: str = "", batch: str = "") -> dict:
    """How many times has this number or batch been reported?"""
    init()
    con = _connect()
    try:
        if nafdac_number:
            by_number = con.execute(
                "SELECT COUNT(*) c, COUNT(DISTINCT batch) b, "
                "COUNT(DISTINCT location) l FROM cases WHERE nafdac = ?",
                (nafdac_number,),
            ).fetchone()
        else:
            by_number = None
        if batch:
            by_batch = con.execute(
                "SELECT COUNT(*) c, COUNT(DISTINCT location) l, "
                "GROUP_CONCAT(DISTINCT location) locs FROM cases WHERE batch = ?",
                (batch,),
            ).fetchone()
        else:
            by_batch = None
    finally:
        con.close()
    return {
        "nafdac": nafdac_number,
        "number_reports": dict(by_number) if by_number else None,
        "batch": batch,
        "batch_reports": dict(by_batch) if by_batch else None,
    }


def stats() -> dict:
    init()
    con = _connect()
    try:
        total = con.execute("SELECT COUNT(*) c FROM cases").fetchone()["c"]
        open_ = con.execute(
            "SELECT COUNT(*) c FROM cases WHERE status='open'").fetchone()["c"]
        high = con.execute(
            "SELECT COUNT(*) c FROM cases WHERE band='high_risk'").fetchone()["c"]
        docs = con.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        today = date.today().isoformat()
        overdue = con.execute(
            "SELECT COUNT(*) c FROM cases WHERE status='open' "
            "AND next_action_at IS NOT NULL AND next_action_at < ?",
            (today,),
        ).fetchone()["c"]
        due_today = con.execute(
            "SELECT COUNT(*) c FROM cases WHERE status='open' "
            "AND next_action_at = ?", (today,),
        ).fetchone()["c"]
    finally:
        con.close()
    return {"cases": total, "open": open_, "high_risk": high,
            "documents": docs, "overdue": overdue, "due_today": due_today}


def reset() -> None:
    """Demo helper: clear everything."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    for suffix in ("-wal", "-shm"):
        p = DB_PATH + suffix
        if os.path.exists(p):
            os.remove(p)
    init()
