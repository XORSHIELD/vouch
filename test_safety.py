"""
Safety-report tests. Round-trips submissions against an isolated data dir.

Run:  python test_safety.py
"""

from __future__ import annotations

import os
import shutil
import tempfile

import safety

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


TMP = tempfile.mkdtemp(prefix="vouch-safety-")
os.environ["VOUCH_AUDIT_LOG"] = os.path.join(TMP, "audit.log")
safety.DATA_DIR = os.path.join(TMP, "data")
safety.DB_PATH = os.path.join(safety.DATA_DIR, "vouch.sqlite")
safety.ALERT_FILE = os.path.join(safety.DATA_DIR, "alerts.json")
safety.PHOTO_DIR = os.path.join(safety.DATA_DIR, "safety")

print("safety reports")


def pack(seller: str, **kw: str) -> dict:
    base = {
        "product_name": "AC-DREX Tablet",
        "nafdac_number": "A11-0551",
        "batch": "B7",
        "issue": "label or seal problem",
        "notes": "The seal looked resealed at the corner of the pack.",
        "seller": seller,
    }
    base.update(kw)
    return base


out = safety.submit(pack("Stall One"))
rid = out.get("id", "")
check("submit stores", out.get("ok") is True and bool(rid), str(out)[:200])
got = safety.get(rid)
check("get reads back",
      bool(got) and got.get("batch") == "B7", str(got)[:200])
check("list finds it",
      any(r.get("id") == rid for r in safety.list_reports(limit=10)))
check("summary counts it", safety.summary().get("reports") == 1,
      str(safety.summary().get("reports")))

with_photo = safety.submit(pack("Stall Two", batch="B8"),
                           photo=b"\xff\xd8\xff" + b"0" * 100)
pp = safety.photo_path(with_photo["id"])
check("photo round-trips", bool(pp) and os.path.exists(pp[0]), str(pp))

safety.submit(pack("Stall Three"))
safety.submit(pack("Stall Four"))
pats = safety.patterns()
check("three same-batch rows raise a batch pattern",
      any(p.get("kind") == "batch" and p.get("batch") == "B7"
          for p in pats),
      str(pats)[:200])

safety.reset()
check("reset empties the store",
      safety.summary().get("reports") == 0
      and safety.list_reports(limit=10) == [])

try:
    os.remove(safety.DB_PATH)
    closed = True
except OSError:
    closed = False
check("no leaked db handle after every call", closed,
      "Windows refuses to delete a sqlite file with an open connection; "
      "POSIX always passes")
print("moderated alerts")
_r1 = safety.submit(pack("Tayo Stores", batch="PX9",
                         notes="Tablets crumbled and the foil looked reglued."))
_r2 = safety.submit(pack("Tayo Stores", batch="PX9",
                         notes="Same stall, same batch, expiry scraped off."))
_r3 = safety.submit(pack("Other Shop", batch="ZZ1",
                         notes="Seal was already broken on arrival here."))
_q = safety.review_queue()
check("submissions queue for review",
      {r["id"] for r in _q} >= {_r1["id"], _r2["id"], _r3["id"]},
      str([r["id"] for r in _q]))
check("missing review reads nothing",
      safety.review("rpt_nope", True) is None)
check("approve flags the report",
      safety.review(_r1["id"], True, "insp")["reviewed"] == 1)
check("dismissed leaves the queue",
      safety.review(_r3["id"], False, "insp")["reviewed"] == -1
      and _r3["id"] not in {r["id"] for r in safety.review_queue()})
try:
    safety.publish_alert({"product_name": "AC-DREX Tablet", "batch": "PX9",
                          "reason": "", "report_ids": [_r1["id"]]})
    check("reason required", False)
except ValueError:
    check("reason required", True)
try:
    safety.publish_alert({"product_name": "AC-DREX Tablet", "batch": "PX9",
                          "reason": "x", "report_ids": [_r2["id"]]})
    check("unapproved link refused", False)
except ValueError:
    check("unapproved link refused", True)
_pub = safety.publish_alert(
    {"product_name": "AC-DREX Tablet", "nafdac_number": "A11-0551",
     "batch": "PX9", "reason": "Two approved submissions name this batch.",
     "report_ids": [_r1["id"]]}, "insp")
check("publish returns an id",
      _pub["ok"] and _pub["id"].startswith("alt_"), str(_pub))
_merged = safety.published_alerts(batch="PX9")
check("published alert listed",
      any(a["id"] == _pub["id"] and a.get("local") is True
          for a in _merged["alerts"]),
      str(_merged["alerts"]))
check("configured by community alone", _merged["configured"] is True)
_bw = safety.batch_watch(batch="PX9")
check("batch watch matches community alert",
      _bw["status"] == "alert_match"
      and any(m.get("local") for m in _bw["alerts"]), _bw["status"])

shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
