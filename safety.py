"""
Vouch — Nigeria Product Safety Intelligence.

The last mile of consumer protection is not another score. It is a shared record
of what has already gone wrong: official alerts, the batch numbers they name, and
the reports people send in when a pack looks wrong.

This module is deliberately small and local:

  * Official alerts are read from a JSON file if one is configured. Vouch does
    not invent an alert, and it says so when no source is connected.
  * User reports are stored in the same SQLite file as cases, on this machine.
    They are submissions, not findings. Nothing here is published automatically.
  * A pattern is raised only when several separate submissions concern the same
    batch or seller. That is a reason to look, never a finding that a named
    person or shop is guilty.

No network calls. No background jobs. Every answer says where it came from.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import time
from typing import Any

DATA_DIR = os.path.join(os.environ.get("VOUCH_DATA_DIR")
                        or os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "vouch.sqlite")
ALERT_FILE = os.path.join(DATA_DIR, "alerts.json")
PHOTO_DIR = os.path.join(DATA_DIR, "safety")

MAX_PHOTO = 8 * 1024 * 1024
PATTERN_MIN = 3
PATTERN_DAYS = 180
ISSUES = [
    "suspected counterfeit",
    "expired or wrong date",
    "damaged or badly stored",
    "label or seal problem",
    "adverse reaction",
    "other",
]
NO_SOURCE = (
    "No official alert source is configured. Vouch does not invent alerts; "
    "add data/alerts.json or connect a regulator feed to enable this layer."
)


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init() -> None:
    con = _connect()
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS safety_reports ("
            "  id TEXT PRIMARY KEY,"
            "  created_at REAL NOT NULL,"
            "  product_name TEXT NOT NULL DEFAULT '',"
            "  nafdac TEXT NOT NULL DEFAULT '',"
            "  batch TEXT NOT NULL DEFAULT '',"
            "  barcode TEXT NOT NULL DEFAULT '',"
            "  seller TEXT NOT NULL DEFAULT '',"
            "  location TEXT NOT NULL DEFAULT '',"
            "  issue TEXT NOT NULL DEFAULT '',"
            "  notes TEXT NOT NULL DEFAULT '',"
            "  contact TEXT NOT NULL DEFAULT '',"
            "  photo TEXT NOT NULL DEFAULT '',"
            "  media_type TEXT NOT NULL DEFAULT '',"
            "  reviewed INTEGER NOT NULL DEFAULT 0)"
        )
        con.execute("CREATE INDEX IF NOT EXISTS safety_batch ON safety_reports(nafdac, batch)")
        con.execute("CREATE INDEX IF NOT EXISTS safety_seller ON safety_reports(seller)")
    finally:
        con.close()


def _clean(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _number(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())[:24]


def _batch(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())[:32]


def _like(value: str) -> str:
    """A LIKE pattern that matches the literal text, not its wildcards."""
    return ("%" + str(value or "").replace("\\", "\\\\")
            .replace("%", "\\%").replace("_", "\\_") + "%")


def _nafdac(value: Any) -> str:
    """One canonical spelling of a registration number for storage and matching.

    Display keeps the familiar dashed form where the registry parser can produce
    it. The series itself is never converted: A4-x and 04-x are different products.
    """
    raw = _clean(value, 40)
    if not raw:
        return ""
    try:
        import registry as R
        found = R.normalise(raw)
        if found:
            return found[0]
    except Exception:
        pass
    return re.sub(r"[^A-Z0-9-]", "", raw.upper())[:40]


def new_id() -> str:
    return "rpt_" + secrets.token_hex(6)


def _row(row: sqlite3.Row) -> dict:
    body = {key: row[key] for key in row.keys()}
    body.pop("photo", None)
    body.pop("media_type", None)
    body.pop("contact", None)
    body["has_contact"] = bool(row["contact"])
    body["has_photo"] = bool(row["photo"])
    if row["photo"]:
        body["photo_url"] = "/api/safety/photo/" + row["id"]
    body["at"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["created_at"]))
    return body


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------

def alerts(query: str = "", nafdac: str = "", batch: str = "") -> dict:
    """Read configured official alerts. An empty source is a real answer."""
    if not os.path.exists(ALERT_FILE):
        return {"configured": False, "source": "", "updated": "", "alerts": [],
                "note": NO_SOURCE}
    try:
        with open(ALERT_FILE, "r", encoding="utf-8-sig") as fh:
            body = json.load(fh)
        records = body if isinstance(body, list) else body.get("alerts", [])
        source = "" if isinstance(body, list) else _clean(body.get("source"), 160)
        updated = "" if isinstance(body, list) else _clean(body.get("updated"), 40)
        if not isinstance(records, list):
            raise ValueError("alerts must be a list")
    except Exception as exc:
        return {"configured": True, "source": "", "updated": "", "alerts": [],
                "note": f"The alert file could not be read ({exc}). Vouch will not guess."}

    q = _clean(query, 80).lower()
    want_n = _number(nafdac)
    want_b = _batch(batch)
    out: list[dict] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        rec = {
            "id": _clean(item.get("id"), 60),
            "product_name": _clean(item.get("product_name"), 160),
            "nafdac_number": _clean(item.get("nafdac_number"), 40),
            "batch": _clean(item.get("batch"), 40),
            "barcode": _clean(item.get("barcode"), 40),
            "manufacturer": _clean(item.get("manufacturer"), 160),
            "reason": _clean(item.get("reason"), 300),
            "published": _clean(item.get("published"), 40),
            "url": _clean(item.get("url"), 400),
            "source": _clean(item.get("source") or source, 160),
            "demo": bool(item.get("demo")),
        }
        if want_n and _number(rec["nafdac_number"]) != want_n:
            continue
        if want_b and _batch(rec["batch"]) != want_b:
            continue
        if q:
            hay = " ".join(str(rec[k]) for k in
                           ("product_name", "nafdac_number", "batch", "barcode",
                            "manufacturer", "reason")).lower()
            if q not in hay:
                continue
        out.append(rec)
    return {
        "configured": True,
        "source": source,
        "updated": updated,
        "alerts": out[:50],
        "count": len(out),
        "note": ("Matched against the configured alert file. An alert names a batch; "
                 "it is not a chemical test of every pack in circulation."),
    }


def match_alerts(nafdac: str = "", batch: str = "", barcode: str = "") -> list[dict]:
    """Exact matches only, with the level of the match recorded.

    A batch match is only ever a batch match. A registration number alone must
    not inherit an alert that names a different batch, and a barcode-only lookup
    must not inherit an alert that names no barcode at all. The caller can then
    say which identifier actually matched instead of overstating the evidence.
    """
    if not (nafdac or batch or barcode):
        return []
    found = alerts(nafdac=nafdac, batch=batch)["alerts"]
    want_batch = _batch(batch)
    want_code = _number(barcode)
    out: list[dict] = []
    for item in found:
        rec = dict(item)
        rec_batch = _batch(rec.get("batch"))
        rec_code = _number(rec.get("barcode"))
        if want_batch:
            if rec_batch != want_batch:
                continue
            rec["match_level"] = "batch"
        elif want_code:
            # A barcode is specific: require it, even when the alert also names
            # a batch we do not know.
            if rec_code != want_code:
                continue
            rec["match_level"] = "barcode"
        else:
            # We only know the registration number. An alert about a batch must
            # not be presented as a match to a pack whose batch is unknown.
            if rec_batch or not nafdac:
                continue
            rec["match_level"] = "product"
        out.append(rec)
    return out


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------

def submit(payload: dict, photo: bytes | None = None,
           media_type: str = "image/jpeg") -> dict:
    """Store one user submission. It is evidence to review, not a verdict."""
    init()
    issue = _clean(payload.get("issue"), 60)
    if issue not in ISSUES:
        issue = "other"
    notes = _clean(payload.get("notes"), 1200)
    product = _clean(payload.get("product_name"), 160)
    nafdac = _nafdac(payload.get("nafdac_number"))
    batch = _batch(payload.get("batch"))
    barcode = _number(payload.get("barcode"))
    if not (product or nafdac or barcode or batch):
        raise ValueError("Give at least a product, registration number, barcode or batch.")
    if len(notes) < 12:
        raise ValueError("Describe what you saw in at least a few words.")

    report_id = new_id()
    photo_name = ""
    if photo:
        if len(photo) > MAX_PHOTO:
            raise ValueError("The photo is larger than 8 MB.")
        ext = {"image/png": ".png", "image/webp": ".webp"}.get(media_type, ".jpg")
        os.makedirs(PHOTO_DIR, exist_ok=True)
        photo_name = report_id + ext
        with open(os.path.join(PHOTO_DIR, photo_name), "wb") as fh:
            fh.write(photo)

    con = _connect()
    try:
        con.execute(
            "INSERT INTO safety_reports (id, created_at, product_name, nafdac, batch,"
            " barcode, seller, location, issue, notes, contact, photo, media_type)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (report_id, time.time(), product, nafdac, batch, barcode,
             _clean(payload.get("seller"), 160), _clean(payload.get("location"), 160),
             issue, notes, _clean(payload.get("contact"), 160), photo_name, media_type),
        )
        con.commit()
    finally:
        con.close()
    try:
        import audit
        audit.log("report_submitted", "", report_id)
    except Exception:
        pass
    return {
        "ok": True,
        "id": report_id,
        "message": ("Saved for review on this deployment. It is a submission, not a "
                    "finding: Vouch does not publish it as fact and does not declare "
                    "a seller guilty."),
        "report": get(report_id),
    }


def get(report_id: str) -> dict | None:
    init()
    con = _connect()
    try:
        row = con.execute("SELECT * FROM safety_reports WHERE id = ?",
                          (report_id,)).fetchone()
    finally:
        con.close()
    return _row(row) if row else None


def photo_path(report_id: str) -> tuple[str, str] | None:
    init()
    con = _connect()
    try:
        row = con.execute("SELECT photo, media_type FROM safety_reports WHERE id = ?",
                          (report_id,)).fetchone()
    finally:
        con.close()
    if not row or not row["photo"]:
        return None
    path = os.path.join(PHOTO_DIR, os.path.basename(row["photo"]))
    return (path, row["media_type"] or "image/jpeg") if os.path.exists(path) else None


def list_reports(limit: int = 30, nafdac: str = "", batch: str = "",
                 seller: str = "", query: str = "", barcode: str = "") -> list[dict]:
    init()
    sql = "SELECT * FROM safety_reports"
    where: list[str] = []
    args: list[Any] = []
    if nafdac:
        where.append("nafdac = ?")
        args.append(_nafdac(nafdac))
    if batch:
        where.append("batch = ?")
        args.append(_batch(batch))
    if barcode:
        where.append("barcode = ?")
        args.append(_number(barcode))
    if seller:
        where.append("seller LIKE ? ESCAPE '\\'")
        args.append(_like(seller))
    if query:
        where.append("(product_name LIKE ? ESCAPE '\\'"
                     " OR notes LIKE ? ESCAPE '\\'"
                     " OR nafdac LIKE ? ESCAPE '\\'"
                     " OR batch LIKE ? ESCAPE '\\'"
                     " OR seller LIKE ? ESCAPE '\\')")
        args.extend([_like(query)] * 5)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(100, int(limit))))
    con = _connect()
    try:
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    return [_row(r) for r in rows]


def patterns(limit: int = 10) -> list[dict]:
    """Groups that deserve a look. Deliberately conservative."""
    init()
    since = time.time() - PATTERN_DAYS * 86400
    out: list[dict] = []
    con = _connect()
    try:
        rows = con.execute(
            "SELECT MIN(nafdac) AS nafdac, batch, COUNT(*) AS n, COUNT(DISTINCT id) AS ids,"
            " MIN(created_at) AS first, MAX(created_at) AS last"
            " FROM safety_reports WHERE created_at >= ? AND batch <> ''"
            " GROUP BY batch HAVING n >= ? ORDER BY n DESC LIMIT ?",
            (since, PATTERN_MIN, limit),
        ).fetchall()
        for row in rows:
            out.append({
                "kind": "batch",
                "nafdac": row["nafdac"],
                "batch": row["batch"],
                "reports": row["n"],
                "level": "review",
                "note": (f"{row['n']} separate submissions name this batch. That is a "
                         "reason to investigate, not a finding that any seller is guilty."),
                "first": time.strftime("%Y-%m-%d", time.localtime(row["first"])),
                "last": time.strftime("%Y-%m-%d", time.localtime(row["last"])),
            })
        seller_rows = con.execute(
            "SELECT seller, location, COUNT(*) AS n, MIN(created_at) AS first,"
            " MAX(created_at) AS last FROM safety_reports"
            " WHERE created_at >= ? AND seller <> '' GROUP BY seller, location"
            " HAVING n >= ? ORDER BY n DESC LIMIT ?",
            (since, PATTERN_MIN, limit),
        ).fetchall()
        for row in seller_rows:
            out.append({
                "kind": "seller",
                "seller": row["seller"],
                "location": row["location"],
                "reports": row["n"],
                "level": "review",
                "note": (f"{row['n']} submissions name this seller. This is a pattern "
                         "for review only; it is not a public accusation."),
                "first": time.strftime("%Y-%m-%d", time.localtime(row["first"])),
                "last": time.strftime("%Y-%m-%d", time.localtime(row["last"])),
            })
    finally:
        con.close()
    return out


def batch_watch(nafdac: str = "", batch: str = "", barcode: str = "") -> dict:
    """Everything the shared layer knows about this exact pack identity."""
    n = _nafdac(nafdac)
    b = _batch(batch)
    code = _number(barcode)
    matches = match_alerts(nafdac=n, batch=b, barcode=code)
    if b:
        reports = list_reports(limit=100, batch=b)
        if n:
            # A submission without a registration number still concerns the
            # batch; one with a different registration number does not.
            reports = [r for r in reports if not r.get("nafdac") or r["nafdac"] == n]
        reports = reports[:20]
    elif n:
        reports = list_reports(limit=20, nafdac=n)
    elif code:
        reports = list_reports(limit=20, barcode=code)
    else:
        reports = []
    group = [p for p in patterns() if
             (p["kind"] == "batch" and b and p["batch"] == b)]
    source = alerts()
    if matches:
        status = "alert_match"
        levels = {m.get("match_level") for m in matches}
        if "batch" in levels:
            message = ("This batch matches a configured official alert. Do not use it; "
                       "follow the alert's guidance.")
        elif "barcode" in levels:
            message = ("This barcode matches a configured official alert. Check the "
                       "alert's batch before concluding; do not use a pack named in the alert.")
        else:
            message = ("This product or registration number matches a configured official "
                       "alert. The alert does not state a batch, so it cannot be tied to the "
                       "pack in your hand; check with the manufacturer or regulator.")
    elif group:
        status = "pattern"
        message = "Several submissions name this batch. It is flagged for review, not proven counterfeit."
    elif reports:
        status = "reports"
        message = f"{len(reports)} Vouch submission(s) name this batch. They have not been independently verified."
    elif not source["configured"]:
        status = "no_alert_source"
        message = source["note"]
    else:
        status = "no_match"
        message = ("No match in the configured alert file and no Vouch submissions "
                   "for this batch. That is not proof the pack is genuine.")
    return {
        "status": status,
        "message": message,
        "alerts": matches,
        "alert_source": {
            "configured": source["configured"],
            "source": source.get("source", ""),
            "updated": source.get("updated", ""),
            "note": source.get("note", ""),
        },
        "reports": reports,
        "report_count": len(reports),
        "pattern": group[0] if group else None,
    }


def summary() -> dict:
    init()
    source = alerts()
    con = _connect()
    try:
        total = con.execute("SELECT COUNT(*) FROM safety_reports").fetchone()[0]
    finally:
        con.close()
    return {
        "reports": total,
        "patterns": patterns(),
        "alerts": {
            "configured": source["configured"],
            "source": source.get("source", ""),
            "updated": source.get("updated", ""),
            "count": len(source["alerts"]),
            "note": source.get("note", ""),
        },
        "limits": [
            "Submissions are not verified identities and are not published as findings.",
            "No chemical test happens here; a camera cannot detect a substituted ingredient.",
            "A pattern is a reason to investigate, never a public accusation.",
        ],
    }


def reset() -> None:
    init()
    con = _connect()
    try:
        con.execute("DELETE FROM safety_reports")
        con.commit()
    finally:
        con.close()
    if os.path.isdir(PHOTO_DIR):
        for name in os.listdir(PHOTO_DIR):
            try:
                os.remove(os.path.join(PHOTO_DIR, name))
            except OSError:
                pass
