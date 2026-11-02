"""
Scan-tally tests: anonymous check counts for inspectors.

No network: tallies are local SQLite rows; the wired endpoint is called
with an empty pack so no lookup leaves the machine.

Run:  python test_scans.py
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

TMP = tempfile.mkdtemp(prefix="vouch-scans-")
os.environ["VOUCH_DATA_DIR"] = TMP

import cases
from engine import PackInput

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("tallies")
cases.log_scan(PackInput(nafdac_number="A11-0551", product_name="AC-DREX",
                         seller="Kiosk", location="Lagos"), "review")
cases.log_scan(PackInput(nafdac_number="A11-0551", product_name="AC-DREX"),
               "review")
cases.log_scan(PackInput(nafdac_number="B4-0262"), "high_risk")
st = cases.scan_stats()
check("three scans counted", st["total"] == 3, repr(st)[:200])
check("top product first",
      st["top"][0]["nafdac"] == "A11-0551"
      and st["top"][0]["scans"] == 2, repr(st["top"])[:200])
check("bands split",
      st["by_band"] == {"review": 2, "high_risk": 1},
      repr(st["by_band"]))
con = sqlite3.connect(cases.DB_PATH)
try:
    blob = str(con.execute("SELECT * FROM scan_events").fetchall())
finally:
    con.close()
check("nothing personal stored",
      "Kiosk" not in blob and "Lagos" not in blob, blob[:200])
check("window clamp sane", cases.scan_stats(days=9999)["days"] == 365)

print("wiring")
import server as _server

r = _server.check(_server.PackBody())
check("check still answers", bool(r.get("band")), repr(r)[:120])
st = cases.scan_stats()
check("endpoint tallies the check", st["total"] == 4, repr(st)[:160])
a = _server.analytics(30, {"username": "i", "role": "inspector"})
check("analytics carries scans",
      a.get("scans", {}).get("total") == 4, repr(a.get("scans"))[:160])

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
