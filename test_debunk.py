"""
Forwarded-list tests: viral claims against the mirror and warnings.

No network: the mirror and the alert file are seeded in temp dirs.

Run:  python test_debunk.py
"""

from __future__ import annotations

import json
import os
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="vouch-debunk-")
os.environ["VOUCH_CACHE_DIR"] = os.path.join(TMP, "cache")
os.environ["VOUCH_DATA_DIR"] = os.path.join(TMP, "data")

import debunk
import registry as R
import safety

fails = 0

ROWS = [
    {"product_id": 101, "NAFDAC": "A11-0551",
     "product_name": "AC-DREX Tablet", "applicant_name": "A.C. Drugs Ltd",
     "status": "Active", "approval_date": "2020-02-10",
     "expiry_date": "2025-02-09", "composition": "Paracetamol 500mg",
     "pack_size": "1 x 100", "form_name": "Tablet",
     "marketing_category_id": "OTC", "category_name": "Medicine"},
    {"product_id": 102, "NAFDAC": "B4-0262",
     "product_name": "Old Remedy Syrup", "applicant_name": "Old Maker Ltd",
     "status": "Inactive", "approval_date": "2010-05-01",
     "expiry_date": "2015-04-30", "composition": "",
     "pack_size": "100ml", "form_name": "Syrup",
     "marketing_category_id": "OTC", "category_name": "Medicine"},
]

con = R._mirror_db()
for _d in ROWS:
    R._mirror_upsert(con, _d, 1)
con.commit()
con.close()
R._sync_put(status="complete", finished_at=time.time(), total=2, fetched=2,
            pass_no=1, error="")

os.makedirs(os.path.dirname(safety.ALERT_FILE), exist_ok=True)
with open(safety.ALERT_FILE, "w", encoding="utf-8") as _fh:
    json.dump({"source": "test", "updated": "today", "alerts": [
        {"id": "021/2026", "reason": "Counterfeit Cowbell Our Milk 12g "
         "sachets found in circulation", "published": "Aug-2026",
         "url": "", "source": "test"}]}, _fh)


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("items")
check("bullets stripped",
      debunk.items_of("1. Cowbell milk\n- AC-DREX\n* B4-0262")
      == ["Cowbell milk", "AC-DREX", "B4-0262"],
      repr(debunk.items_of("1. Cowbell milk\n- AC-DREX\n* B4-0262")))
check("blanks skipped", debunk.items_of("\n\nA11-0551\n\n") == ["A11-0551"])
check("list capped", len(debunk.items_of("x\n" * 99)) == debunk.MAX_ITEMS)

print("checks")
out = debunk.check("A11-0551\nCowbell Our Milk\nXyzzy Nope")
check("ok with three items",
      out["ok"] is True and len(out["items"]) == 3, repr(out)[:200])
by_text = {r["text"]: r for r in out["items"]}
check("number resolves in the mirror",
      by_text["A11-0551"]["register"] == "registered"
      and by_text["A11-0551"]["registered_name"] == "AC-DREX Tablet",
      repr(by_text["A11-0551"])[:200])
check("warning hit by product words",
      len(by_text["Cowbell Our Milk"]["alerts"]) == 1
      and "021/2026" in by_text["Cowbell Our Milk"]["alerts"][0]["id"],
      repr(by_text["Cowbell Our Milk"])[:200])
check("unknown stays unknown",
      by_text["Xyzzy Nope"]["register"] == ""
      and by_text["Xyzzy Nope"]["name_hits"] == 0
      and by_text["Xyzzy Nope"]["alerts"] == [],
      repr(by_text["Xyzzy Nope"])[:200])
check("counts add up",
      out["counts"] == {"checked": 3, "in_register": 1, "with_alerts": 1,
                        "unmatched": 1}, repr(out["counts"]))
check("honesty note present",
      "not that the product is genuine" in out["note"])

print("refusals")
out = debunk.check("   \n ")
check("empty list refused",
      out["ok"] is False and "one per line" in out["message"],
      repr(out)[:160])
_real_cache = R.CACHE_DIR
R.CACHE_DIR = os.path.join(TMP, "empty-cache")
try:
    out = debunk.check("A11-0551")
    check("no mirror refuses",
          out["ok"] is False and "not synced" in out["message"],
          repr(out)[:160])
finally:
    R.CACHE_DIR = _real_cache

print("endpoint")
import server as _server

_existing = getattr(_server, "debunk_list", None)
check("endpoint wired", callable(_existing))
if callable(_existing):
    body = _server.DebunkBody(text="A11-0551\nXyzzy Nope")
    r = _existing(body)
    check("endpoint answers",
          r["ok"] is True and r["counts"]["checked"] == 2, repr(r)[:200])

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
