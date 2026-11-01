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
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
