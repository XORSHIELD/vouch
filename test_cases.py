"""
Case store tests: lifecycle, close guards, queue math.

No server, no network: cases run against a temporary database.

Run:  python test_cases.py
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

TMP = tempfile.mkdtemp(prefix="vouch-cases-")
os.environ["VOUCH_DATA_DIR"] = TMP

import cases
from engine import Assessment, PackInput

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


def _assess(band: str = "review", score: float = 1.5) -> Assessment:
    return Assessment(band=band, headline="test headline", checks=[],
                      actions=[], score=score, registry=None, input={})


def _pack(**kw) -> PackInput:
    base = {"nafdac_number": "A11-0551", "product_name": "Test Tablet",
            "batch": "B1", "seller": "Shop", "location": "Lagos",
            "price": "100", "exp_date": "EXP 2028"}
    base.update(kw)
    return PackInput(**base)


def _event_count() -> int:
    con = sqlite3.connect(cases.DB_PATH)
    try:
        return con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        con.close()


print("lifecycle")
c = cases.create_case(_pack(), _assess())
check("create returns an id", c["id"].startswith("VC-"), str(c["id"]))
back = cases.get_case(c["id"])
check("get reads it back",
      (back or {}).get("product") == "Test Tablet", str(back)[:200])
check("missing id reads nothing", cases.get_case("VC-NOPE") is None)

print("close guards")
check("close missing id returns nothing",
      cases.close_case("VC-NOPE", "nothing") is None)
check("close missing id writes no events", _event_count() == 1,
      f"events={_event_count()}")
closed = cases.close_case(c["id"], "refunded")
check("close stores outcome",
      (closed or {}).get("outcome") == "refunded"
      and (closed or {}).get("status") == "closed", str(closed)[:200])
check("closed case leaves the queue",
      all(q["id"] != c["id"] for q in cases.queue()))

print("queue math")
c2 = cases.create_case(_pack(batch="B2"), _assess("high_risk", 6.0))
check("open case is queued",
      any(q["id"] == c2["id"] for q in cases.queue()))
marked = cases.mark_step(c2["id"], 7, "filed")
check("mark step works", bool(marked))
snoozed = cases.snooze(c2["id"], 5)
check("snooze works", bool(snoozed and snoozed.get("next_action_at")))
stats = cases.stats()
check("stats count cases", stats["cases"] == 2, str(stats))

print("reset")
cases.reset()
check("reset empties the store", cases.stats()["cases"] == 0)

print("ownership")
alice = cases.create_case(_pack(batch="AO1"), _assess(), username="alice")
bob = cases.create_case(_pack(batch="BO1"), _assess(), username="bob")
legacy = cases.create_case(_pack(batch="LG1"), _assess())
check("create records the owner",
      cases.get_case(alice["id"])["username"] == "alice")
mine = {c["id"] for c in
        cases.list_cases(username="alice", role="consumer")}
check("consumers see own plus legacy",
      alice["id"] in mine and bob["id"] not in mine, str(mine))
check("queue is filtered too",
      all(q["id"] != bob["id"]
          for q in cases.queue(username="alice", role="consumer")))
check("inspectors read everything",
      bob["id"] in {c["id"] for c in
                    cases.list_cases(username="root", role="inspector")})
check("admins read everything",
      bob["id"] in {c["id"] for c in
                    cases.list_cases(username="root", role="admin")})
check("consumer blocked from another user's case",
      cases.can_access("bob", "alice", "consumer") is False)
check("consumer reads own",
      cases.can_access("alice", "alice", "consumer") is True)
check("legacy rows stay visible",
      cases.can_access("", "alice", "consumer") is True)
check("inspector reads all",
      cases.can_access("bob", "root", "inspector") is True)
check("unknown role reads nothing foreign",
      cases.can_access("bob", "mallory", "nobody") is False)
check("cluster counts scope to the owner",
      cases.cluster("A11-0551", username="alice",
                    role="consumer")["number_reports"]["c"] == 2,
      str(cases.cluster("A11-0551")))
check("cluster stays global for unscoped callers",
      cases.cluster("A11-0551")["number_reports"]["c"] == 3)

print("username migration")
migdir = os.path.join(TMP, "mig")
os.makedirs(os.path.join(migdir, ".data"), exist_ok=True)
olddb = os.path.join(migdir, ".data", "vouch.sqlite")
_m = sqlite3.connect(olddb)
_m.executescript(
    "CREATE TABLE cases (id TEXT PRIMARY KEY, created_at TEXT NOT NULL,"
    " band TEXT NOT NULL, score REAL NOT NULL, headline TEXT,"
    " nafdac TEXT, product TEXT, manufacturer TEXT, batch TEXT,"
    " seller TEXT, location TEXT, price TEXT, exp_date TEXT,"
    " status TEXT NOT NULL DEFAULT 'open', outcome TEXT,"
    " next_action_at TEXT, payload TEXT NOT NULL);"
    "CREATE TABLE documents (id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " case_id TEXT NOT NULL, kind TEXT NOT NULL, title TEXT,"
    " body TEXT NOT NULL, created_at TEXT NOT NULL);"
    "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " case_id TEXT NOT NULL, at TEXT NOT NULL, kind TEXT NOT NULL,"
    " note TEXT);"
    "INSERT INTO cases (id, created_at, band, score, headline, nafdac,"
    " product, manufacturer, batch, seller, location, price, exp_date,"
    " status, outcome, next_action_at, payload) VALUES ('VC-OLD',"
    " '2026-01-01T09:00:00', 'review', 1.5, 'old', 'A11-0551', 'P', 'M',"
    " 'B0', 'S', 'L', '1', '', 'open', NULL, NULL, '{}');")
_m.commit()
_m.close()
_real_data, _real_db = cases.DATA_DIR, cases.DB_PATH
cases.DATA_DIR, cases.DB_PATH = os.path.dirname(olddb), olddb
try:
    got = cases.get_case("VC-OLD")
    check("pre-migration case reads",
          got is not None and got["username"] == "")
    _cols = [r[1] for r in sqlite3.connect(olddb).execute(
        "PRAGMA table_info(cases)").fetchall()]
    check("username column added", "username" in _cols, str(_cols))
finally:
    cases.DATA_DIR, cases.DB_PATH = _real_data, _real_db

print("export rows")
_all = cases.export_rows()
check("unscoped export sees everything", len(_all) == 3, str(len(_all)))
_mine = {r["id"] for r in cases.export_rows(username="alice", role="consumer")}
check("scoped export hides other users",
      _mine == {alice["id"], legacy["id"]}, str(sorted(_mine)))
check("staff export sees everything",
      len(cases.export_rows(username="root", role="inspector")) == 3)
check("export rows carry owner + outcome",
      all("username" in r and "outcome" in r for r in _all))

print("analytics")
_an = cases.analytics()
check("every case counted once",
      sum(_an["by_band"].values()) == 3, str(_an["by_band"]))
check("status split sane",
      _an["by_status"].get("open", 0) == 3, str(_an["by_status"]))
check("reporters listed",
      _an["by_reporter"].get("alice") == 1
      and _an["by_reporter"].get("") == 1,
      str(_an["by_reporter"]))
check("days counted", sum(_an["per_day"].values()) == 3,
      str(_an["per_day"]))
_lg_batch = cases.get_case(legacy["id"])["batch"]
check("top batches ranked",
      any(t["batch"] == _lg_batch for t in _an["top_batches"]),
      str(_an["top_batches"]))
cases.close_case(alice["id"], "fixed")
_an2 = cases.analytics()
check("outcomes counted",
      _an2["outcomes"].get("fixed") == 1, str(_an2["outcomes"]))

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
