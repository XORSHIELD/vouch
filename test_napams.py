"""
NAPAMS reply-reader tests: pasted portal verdicts, read honestly.

No network: reply texts are inline fixtures (the portal's exact wording
varies, so the reader keys on verdict words, never layout).

Run:  python test_napams.py
"""

from __future__ import annotations

import os
import tempfile

os.environ.pop("MODEL_API_KEY", None)
os.environ.pop("META_API_KEY", None)

import agent
import napams

agent.API_KEY = ""

# Hermetic NAPAMS memory: pastes filed here must never touch the real cache.
_napams_real_cache = napams.CACHE_FILE
_napams_tmp = tempfile.NamedTemporaryFile(
    prefix="napams_test_", suffix=".json", delete=False)
_napams_tmp.close()
napams.CACHE_FILE = _napams_tmp.name

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

print("product details format")
DETAILS = """PRODUCT DETAILS
The Certificate of Product Registration has expired
Product Name: CWAY DRINKING WATER (01-3892)(01-3892)
Product Category: Package Water
Product Source: Nigerian Products
Manufacturer: CWAY NIGERIA DRINKING WATER SCIENCE AND TECHNOLOGY CO. LIMITED
Date Approved: 2021-10-28
Expiry Date: 2026-10-27
NAFDAC No: 01-3892
Active Ingredient: WATER"""
r = napams.read_reply(DETAILS, "01-3892")
check("details block parsed",
      r["verdict"] == napams.EXPIRED and r["about"] == "01-3892"
      and r["details"].get("product") == "CWAY DRINKING WATER"
      and r["details"].get("category") == "Package Water"
      and r["details"].get("manufacturer", "").startswith("CWAY NIGERIA")
      and r["details"].get("approved") == "2021-10-28"
      and r["details"].get("expiry") == "2026-10-27"
      and r["matches"] is True, repr(r)[:300])
LIVE = DETAILS.replace(
    "The Certificate of Product Registration has expired\n", "")
r = napams.read_reply(LIVE, "01-3892")
check("details without banner read as listed",
      r["verdict"] == napams.REGISTERED
      and r["details"].get("product") == "CWAY DRINKING WATER",
      repr(r)[:200])
_hist = [{"role": "user", "text": "01-3892"},
         {"role": "assistant", "text": "No registry record here."}]
r = agent.chat(DETAILS, _hist)
check("paste answered with the portal's details",
      "CWAY DRINKING WATER" in r["answer"]
      and "CWAY NIGERIA" in r["answer"]
      and "2026-10-27" in r["answer"]
      and "certificate has expired" in r["answer"]
      and "replaces my earlier" in r["answer"],
      r["answer"][:500])
r = agent.chat(DETAILS, [])
check("fresh paste skips the bridge",
      "CWAY DRINKING WATER" in r["answer"]
      and "replaces my earlier" not in r["answer"],
      r["answer"][:300])
check("no binary language in details answers",
      not any(w in r["answer"].lower() for w in banned),
      r["answer"][:300])

print("crowd memory: shared pastes are filed and recalled")
_real_cache = napams.CACHE_FILE
_tmp = tempfile.NamedTemporaryFile(
    prefix="napams_known_", suffix=".json", delete=False)
_tmp.close()
napams.CACHE_FILE = _tmp.name
try:
    check("empty cache knows nothing",
          napams.known("01-3892") is None)
    filed = napams.file_details(napams.read_reply(DETAILS, "01-3892"))
    check("details paste is filed",
          bool(filed) and filed["product"] == "CWAY DRINKING WATER"
          and filed["number"] == "01-3892"
          and filed["verdict"] == napams.EXPIRED, repr(filed)[:250])
    hit = napams.known("013892")
    check("filed number recalled, digits normalised",
          hit is not None and hit["hits"] == 1
          and hit["product"] == "CWAY DRINKING WATER"
          and hit["maker"].startswith("CWAY NIGERIA")
          and hit["expiry"] == "2026-10-27", repr(hit)[:250])
    napams.file_details(napams.read_reply(LIVE, "01-3892"))
    check("second paste bumps the tally",
          napams.known("01-3892")["hits"] == 2)
    bare = napams.read_reply(
        "NAPAMS: NAFDAC Reg No 77-0000 is registered.", "77-0000")
    check("bare verdict paste is not filed",
          napams.file_details(bare) is None
          and napams.known("77-0000") is None, repr(bare)[:150])
    wrong = napams.read_reply(DETAILS, "99-9999")
    check("wrong-number paste is not filed",
          napams.file_details(wrong) is None
          and napams.known("99-9999") is None, repr(wrong)[:150])
    DETAILS2 = """PRODUCT DETAILS
Product Name: EXAMPLE SACHET WATER (99-4321)
Product Category: Package Water
Product Source: Nigerian Products
Manufacturer: EXAMPLE WATER LTD
Date Approved: 2023-01-15
Expiry Date: 2028-01-14
NAFDAC No: 99-4321
Active Ingredient: WATER"""
    filed2 = napams.file_details(napams.read_reply(DETAILS2, "99-4321"))
    check("second number filed alongside the first",
          bool(filed2) and filed2["verdict"] == napams.REGISTERED
          and napams.known("99-4321")["product"] == "EXAMPLE SACHET WATER"
          and napams.known("01-3892")["product"] == "CWAY DRINKING WATER"
          and napams.known("01-3892")["hits"] == 2, repr(filed2)[:200])
    clash = DETAILS2.replace("EXAMPLE SACHET WATER", "RENAMED SACHET WATER")
    napams.file_details(napams.read_reply(clash, "99-4321"))
    hit2 = napams.known("99-4321")
    check("conflicting paste replaces, tally restarts",
          hit2["product"] == "RENAMED SACHET WATER" and hit2["hits"] == 1,
          repr(hit2)[:200])
    import engine as E
    rep = E.assess(E.PackInput(nafdac_number="01-3892"), offline=True)
    reg_chk = next(c for c in rep.checks if c.id == "registry")
    check("engine answers a remembered number from the shared reply",
          reg_chk.state == E.ATTENTION
          and "NAPAMS listed this number when shared" in reg_chk.evidence,
          reg_chk.evidence[:250])
    acts = [a.kind for a in rep.actions]
    check("remembered number keeps the NAPAMS action",
          "napams" in acts and "nafdac_report" not in acts, repr(acts))
    import sms
    _sms = sms.handle_inbound("NAFDAC 01-3892")
    check("sms answers a remembered number",
          "NAPAMS lists 01-3892 as CWAY DRINKING WATER" in _sms, _sms[:250])
    ans = agent.chat("01-3892", [])["answer"]
    check("chat recalls the shared reply",
          "CWAY DRINKING WATER" in ans
          and "not a live lookup" in ans, ans[:400])
finally:
    napams.CACHE_FILE = _real_cache
    os.unlink(_tmp.name)
napams.CACHE_FILE = _napams_real_cache
os.unlink(_napams_tmp.name)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
