"""
Document-builder tests: shape, content, careful language, escalation.

No network: documents are pure text builders.

Run:  python test_actions.py
"""

from __future__ import annotations

import re
from datetime import date

import actions
from engine import Assessment, PackInput

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


def _a(band: str = "high_risk", score: float = 6.0) -> Assessment:
    return Assessment(band=band, headline="No record of this number",
                      checks=[], actions=[], score=score, registry=None,
                      input={})


def _p() -> PackInput:
    return PackInput(nafdac_number="A99-9999", product_name="Test Syrup",
                     manufacturer="TestCo", batch="B9", seller="Kiosk",
                     location="Lagos", price="500", exp_date="EXP 2027")


print("documents")
docs = actions.all_documents(_p(), _a())
check("three kinds",
      set(docs) == {"nafdac_report", "fccpc_complaint", "seller_demand"},
      str(sorted(docs)))
for kind, d in docs.items():
    check(f"{kind} has a body", len(d.body) > 200, str(len(d.body)))
check("filenames are safe",
      all(re.fullmatch(r"[a-z0-9][a-z0-9._-]*\.txt", d.filename)
          for d in docs.values()),
      str([d.filename for d in docs.values()]))
check("nafdac doc names the number",
      "A99-9999" in docs["nafdac_report"].body)
check("seller doc names the seller",
      "Kiosk" in docs["seller_demand"].body)
banned = re.compile(
    r"\b(fake|counterfeit|genuine|authentic|forged|bogus|illicit)\b",
    re.IGNORECASE)
blobs = " ".join(d.body for d in docs.values())
check("no binary language in documents",
      not banned.search(blobs),
      (banned.search(blobs) or [None])[0] or "")

print("escalation")
hi = actions.escalation_plan(_a("high_risk", 6.0), date(2026, 1, 1))
check("high risk adds a day-1 NAFDAC step",
      [s.day for s in hi] == [0, 0, 1, 7, 14, 21, 30],
      str([s.day for s in hi]))
check("seven steps", len(hi) == 7, str(len(hi)))
check("steps carry dates",
      all(s.due for s in hi), str(hi[0]))
lo = actions.escalation_plan(_a("review", 1.5), date(2026, 1, 1))
check("routine plan skips day 1",
      [s.day for s in lo] == [0, 0, 7, 14, 21, 30],
      str([s.day for s in lo]))
check("every plan starts by preserving evidence",
      "evidence" in hi[0].action.lower() or "evidence" in hi[0].why.lower())

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
