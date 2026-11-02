"""
Sync/mirror tests for the Vouch registry layer.

No network: everything runs against a temporary cache directory, so a synced
mirror must answer lookups offline with the same verdicts as the live path.

Run:  python test_sync.py
"""

from __future__ import annotations

import os
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="vouch-sync-")
os.environ["VOUCH_CACHE_DIR"] = TMP

import registry as R

import audit as _audit
# Sync completion would otherwise land in the real audit log.
_logged: list = []
_audit.log = lambda *a, **k: _logged.append(a)

# Fixture rows shaped exactly like Greenbook API records (field names as
# served), so the mirror path parses them the way live data is parsed.
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

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("mirror store")
con = R._mirror_db()
for d in ROWS:
    R._mirror_upsert(con, d, 1)
con.commit()
con.close()
R._sync_put(status="complete", finished_at=time.time(), total=2, fetched=2,
            pass_no=1, error="")
check("mirror is ready", R.mirror_ready() is True)

print("mirror verdicts (offline: no live fallback allowed)")
res = R.lookup("A11-0551", offline=True)
check("known number -> REGISTERED", res.verdict == R.REGISTERED,
      f"got {res.verdict}: {res.message}")
check("mirror carries the holder",
      bool(res.matched and "A.C. Drugs" in res.matched.manufacturer))

res = R.lookup("A11-0551", manufacturer_on_pack="Wrong Maker Plc",
               offline=True)
check("wrong company -> MISMATCH", res.verdict == R.MISMATCH,
      f"got {res.verdict}")

res = R.lookup("A99-9999", offline=True)
check("unknown number -> NOT_FOUND", res.verdict == R.NOT_FOUND,
      f"got {res.verdict}: {res.message}")

res = R.lookup("B4-0262", offline=True)
check("lapsed number -> INACTIVE", res.verdict == R.INACTIVE,
      f"got {res.verdict}")

print("mirror search shape")
payload = R.search("dre", length=5)
check("name search filters", payload["recordsFiltered"] == 1,
      str(payload.get("recordsFiltered")))
check("totals count the mirror", payload["recordsTotal"] == 2,
      str(payload.get("recordsTotal")))
check("rows are verbatim records",
      (payload["data"] or [{}])[0].get("NAFDAC") == "A11-0551")

print("killed sync recovery")
R._sync_put(status="running", started_at=time.time() - 3600, total=2,
            fetched=2, pass_no=1, error="")
check("stale run not reported running", R.sync_running() is False)
check("mirror stays ready mid-refresh", R.mirror_ready() is True)
_real_fetch = R._fetch


def _fake_fetch(q, length, start):
    if start >= 2:
        return {"recordsTotal": 2, "data": []}
    return {"recordsTotal": 2, "data": [ROWS[0]]}


R._fetch = _fake_fetch
try:
    R.sync_run(page_size=25)
finally:
    R._fetch = _real_fetch
_st = R._sync_get()
check("stale run resumes the same pass",
      _st["status"] == "complete" and _st["pass"] == 1, str(_st))

print("sync status")
st = R.sync_status()
check("status shape",
      st["records"] == 2 and st["ready"] is True
      and st["source"] == R.BASE and st["pass"] == 1, str(st))
check("no sync is running", R.sync_running() is False)
check("fresh mirror is not stale",
      st["stale"] is False and st["age_days"] == 0.0, str(st))
old = R._sync_get()["finished_at"]
R._sync_put(finished_at=time.time() - 8 * 86400)
check("week-old mirror is stale", R.sync_status()["stale"] is True)
R._sync_put(finished_at=old)
check("freshness restored", R.sync_status()["stale"] is False)

print("exact lookup under loose-match flood")
con = R._mirror_db()
for i in range(10):
    R._mirror_upsert(con, {"product_id": 200 + i, "NAFDAC": f"A11-0551{i}",
                           "product_name": f"AAA Decoy {i}",
                           "applicant_name": "Decoy Ltd",
                           "status": "Active"}, 1)
con.commit()
con.close()
t0 = time.time()
res = R.lookup("A11-0551", offline=True)
dt = time.time() - t0
check("exact row found despite 10 loose lookalikes",
      res.verdict == R.REGISTERED, f"got {res.verdict}: {res.message}")
check("indexed lookup answers fast", dt < 0.5, f"{dt:.2f}s")

print("collapsed pass refused")
_logged.clear()
BIG = [{"product_id": 3000 + i, "NAFDAC": f"Z9-{3000 + i}",
        "product_name": f"Bulk Pack {i}",
        "applicant_name": "Bulk Maker Ltd",
        "status": "Active"} for i in range(120)]
con = R._mirror_db()
for d in BIG:
    R._mirror_upsert(con, d, 1)
con.commit()
con.close()
R._sync_put(status="complete", finished_at=time.time(), total=132,
            fetched=132, pass_no=1, error="")
_before = R.sync_status()["finished_at"]
R._fetch = lambda q, length, start: (
    {"recordsTotal": 2, "data": []} if start >= 2
    else {"recordsTotal": 2, "data": ROWS})
try:
    out = R.sync_run(page_size=25)
finally:
    R._fetch = _real_fetch
check("collapsed pass refused",
      "Refusing" in (out.get("error") or "")
      and out["status"] == "complete",
      str({k: out.get(k) for k in ("status", "error")}))
check("mirror stays ready after refusal",
      R.mirror_ready() is True)
check("previous mirror kept",
      R.sync_status()["records"] == 132,
      str(R.sync_status()["records"]))
check("freshness not faked",
      R.sync_status()["finished_at"] == _before)
check("refusal is logged",
      any(e[0] == "sync_refused" for e in _logged), str(_logged))
res = R.lookup("Z9-3005", offline=True)
check("verdicts intact after refusal", res.verdict == R.REGISTERED,
      f"got {res.verdict}: {res.message}")

print("healthy big pass still completes")
BIG2 = [{"product_id": 4000 + i, "NAFDAC": f"Z9-{4000 + i}",
         "product_name": f"Fresh Pack {i}",
         "applicant_name": "Fresh Maker Ltd",
         "status": "Active"} for i in range(120)]
def _big_fetch(q, length, start):
    if start >= len(BIG2):
        return {"recordsTotal": len(BIG2), "data": []}
    return {"recordsTotal": len(BIG2), "data": BIG2[start:start + length]}
R._fetch = _big_fetch
try:
    out = R.sync_run(page_size=25)
finally:
    R._fetch = _real_fetch
check("near-whole pass completes",
      out["status"] == "complete"
      and R.sync_status()["records"] == 120,
      str({k: out.get(k) for k in ("status", "error")}))

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
