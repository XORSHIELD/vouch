"""
SMS-answer tests: short queries in, short honest answers out.

No network: the register mirror is seeded in a temporary cache and the
alert file in a temporary data dir, so every lookup stays local.

Run:  python test_sms.py
"""

from __future__ import annotations

import json
import os
import tempfile

TMP = tempfile.mkdtemp(prefix="vouch-sms-")
os.environ["VOUCH_CACHE_DIR"] = os.path.join(TMP, "cache")
os.environ["VOUCH_DATA_DIR"] = os.path.join(TMP, "data")
os.environ.pop("VOUCH_SMS_SECRET", None)

import time

import registry as R
import safety
import sms

fails = 0

ROWS = [
    {"product_id": 101, "NAFDAC": "A11-0551",
     "product_name": "AC-DREX Tablet", "applicant_name": "A.C. Drugs Ltd",
     "status": "Active", "approval_date": "2020-02-10",
     "expiry_date": "2025-02-09", "composition": "Paracetamol 500mg",
     "pack_size": "1 x 100", "form_name": "Tablet",
     "marketing_category_id": "OTC", "category_name": "Medicine"},
]

con = R._mirror_db()
for _d in ROWS:
    R._mirror_upsert(con, _d, 1)
con.commit()
con.close()
R._sync_put(status="complete", finished_at=time.time(), total=1, fetched=1,
            pass_no=1, error="")

os.makedirs(os.path.dirname(safety.ALERT_FILE), exist_ok=True)
with open(safety.ALERT_FILE, "w", encoding="utf-8") as _fh:
    json.dump({"source": "test", "updated": "today", "alerts": [
        {"id": "043/2026", "nafdac_number": "A11-0551",
         "reason": "Seized consignment under investigation",
         "published": "19-Aug-26", "url": "", "source": "test"}]},
        _fh)


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("nafdac by text")
r = sms.handle_inbound("NAFDAC A11-0551")
check("registered number answered",
      "AC-DREX" in r and "registered" in r, r)
check("no binary language",
      not any(w in r.lower()
              for w in ("fake", "counterfeit", "genuine", "authentic")), r)
check("alert line attached",
      "043/2026" in r, r)
r = sms.handle_inbound("A99-9999")
check("unknown number honest",
      "no record" in r.lower(), r)
check("unknown number points at NAPAMS",
      "NAPAMS" in r and "registration.nafdac.gov.ng" in r, r)

print("gs1 by text")
GTIN = "06260001146105"
r = sms.handle_inbound(f"G (01){GTIN}(17)281231(10)LOT42")
check("gs1 fields read back",
      GTIN in r and "LOT42" in r and "2028-12-31" in r, r)
r = sms.handle_inbound(f"(01){GTIN}(17)281231")
check("gs1 detected without prefix", GTIN in r, r)
r = sms.handle_inbound("G hello")
check("gs1 garbage honest", "nreadable" in r, r)

print("barcode + help")
r = sms.handle_inbound("V 12345678")
check("bad check digit caught", "check digit" in r, r)
r = sms.handle_inbound("hello there")
check("help text", "NAFDAC <number>" in r, r)
r = sms.handle_inbound("")
check("empty helped, not crashed", "NAFDAC <number>" in r, r)

print("secret")
os.environ["VOUCH_SMS_SECRET"] = "s3cret"
check("right secret passes", sms.check_secret("s3cret") is True)
check("wrong secret fails", sms.check_secret("nope") is False)
os.environ.pop("VOUCH_SMS_SECRET", None)
check("no secret configured fails closed",
      sms.check_secret("s3cret") is False)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
