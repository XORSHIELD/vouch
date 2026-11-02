"""
NAPAMS reply-reader tests: pasted portal verdicts, read honestly.

No network: reply texts are inline fixtures (the portal's exact wording
varies, so the reader keys on verdict words, never layout).

Run:  python test_napams.py
"""

from __future__ import annotations

import os

os.environ.pop("MODEL_API_KEY", None)
os.environ.pop("META_API_KEY", None)

import agent
import napams

agent.API_KEY = ""

fails = 0

REG = ("NAPAMS Product Verification. NAFDAC Reg No: 01-3892. "
       "Product: CWAY Table Water. Status: Registered.")
NEG = ("NAPAMS verification: no record found for NAFDAC Number 01-9999. "
       "Please confirm the digits and try again.")
NOTREG = ("This product is not registered with NAFDAC. "
          "Certificate Number 01-9999.")
MIXED = ("NAPAMS: product 01-3892 registered; certificate invalid, "
         "reprint required.")
OTHER = "NAPAMS: NAFDAC Reg No 01-0001 is registered."


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("reading")
r = napams.read_reply(REG, "01-3892")
check("registered reply read",
      r["verdict"] == napams.REGISTERED and r["about"] == "01-3892",
      repr(r)[:160])
r = napams.read_reply(NEG, "01-9999")
check("no-record reply read",
      r["verdict"] == napams.NOT_FOUND and r["about"] == "01-9999",
      repr(r)[:160])
r = napams.read_reply(NOTREG, "01-9999")
check("'not registered' is negative, not positive",
      r["verdict"] == napams.NOT_FOUND, repr(r)[:160])
r = napams.read_reply(MIXED, "01-3892")
check("mixed signals stay unclear",
      r["verdict"] == napams.UNCLEAR, repr(r)[:160])
r = napams.read_reply(OTHER, "01-3892")
check("reply about another number flagged",
      r["verdict"] == napams.WRONG_NUMBER and r["about"] == "01-0001",
      repr(r)[:160])
r = napams.read_reply("NAPAMS portal maintenance tonight", "01-3892")
check("verdictless paste unclear",
      r["verdict"] == napams.UNCLEAR, repr(r)[:160])
r = napams.read_reply("", "")
check("empty never crashes",
      r["verdict"] == napams.UNCLEAR and r["quote"] == "")

print("agent flow")
r = agent.chat(REG, [{"role": "user", "text": "01-3892"}])
check("paste answered from the portal's words",
      "NAPAMS says 01-3892 is registered" in r["answer"]
      and "portal's statement" in r["answer"]
      and "01-3892" in r["answer"], r["answer"][:300])
r = agent.chat(NEG, [{"role": "user", "text": "01-9999"}])
check("portal no-record passed on",
      "NAPAMS says it has no record of 01-9999" in r["answer"],
      r["answer"][:300])
r = agent.chat("can you tell me more about the NAPAMS portal", [])
check("questions about NAPAMS fall through",
      "NAPAMS says" not in r["answer"], r["answer"][:200])
banned = ("fake", "counterfeit", "genuine", "authentic")
ours = agent.chat(REG, [{"role": "user", "text": "01-3892"}])["answer"]
check("no binary language in our words",
      not any(w in ours.lower() for w in banned), ours[:300])

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
