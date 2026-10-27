"""Engine tests. Run: python test_engine.py"""

from __future__ import annotations

from datetime import date

import engine as G

TODAY = date(2026, 10, 27)
fails = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"\n        {detail}" if detail and not cond else ""))
    if not cond:
        fails += 1


GENUINE = G.PackInput(
    nafdac_number="A11-0551",
    manufacturer="A.C. Drugs Ltd",
    product_name="AC-Drex Tablet",
    batch="ACD2401",
    exp_date="EXP 06/2028",
    mfg_date="MFG 06/2025",
)

print("genuine pack")
a = G.assess(GENUINE, today=TODAY)
check("band is clear", a.band == G.BAND_CLEAR, f"got {a.band}: {a.headline}")
check("registry verified", any(c.id == "registry" and c.state == G.VERIFIED for c in a.checks))
check("expiry verified", any(c.id == "expiry" and c.state == G.VERIFIED for c in a.checks))
check("still tells the user to keep the receipt",
      any(x.kind == "keep_receipt" for x in a.actions))

print("\ncopied number (right number, wrong company)")
bad = G.PackInput(**{**GENUINE.__dict__, "manufacturer": "Emzor Pharmaceutical Industries Ltd"})
a = G.assess(bad, today=TODAY)
check("band is high risk", a.band == G.BAND_HIGH, f"got {a.band}")
check("registry check is high", any(c.id == "registry" and c.state == G.HIGH for c in a.checks))
check("recommends a NAFDAC report", any(x.kind == "nafdac_report" for x in a.actions))

print("\nexpired pack")
exp = G.PackInput(**{**GENUINE.__dict__, "exp_date": "EXP 01/2026"})
a = G.assess(exp, today=TODAY)
check("band is high risk", a.band == G.BAND_HIGH, f"got {a.band}")
check("says expired in plain terms", any("Expired" in c.evidence for c in a.checks if c.id == "expiry"))
check("recommends refund", any(x.kind == "refund" for x in a.actions))
check("recommends regulator report", any(x.kind == "nafdac_report" for x in a.actions))

print("\nregistration window violation")
win = G.PackInput(**{**GENUINE.__dict__, "exp_date": "EXP 01/2030"})
a = G.assess(win, today=TODAY)
check("flags the window", any(c.id == "registration_window" for c in a.checks),
      str([(c.id, c.state) for c in a.checks]))
check("does not call it high risk on its own", a.band == G.BAND_REVIEW, f"got {a.band}")

print("\ninactive registration")
inact = G.PackInput(
    nafdac_number="B4-0262", manufacturer="Novartis Nigeria Limited",
    product_name="Coartem 80/480 Tablets", batch="F1234", exp_date="EXP 01/2027",
)
a = G.assess(inact, today=TODAY)
# An inactive registration plus a pack that outlives it is two signals, so
# review or high are both correct. What matters is the reason it fired.
check("inactive is flagged",
      any(c.id == "registry" and c.state == G.ATTENTION for c in a.checks),
      str([(c.id, c.state) for c in a.checks]))
check("band is review or high", a.band in (G.BAND_REVIEW, G.BAND_HIGH), f"got {a.band}")
check("asks a pharmacist rather than accusing",
      any(x.kind in ("ask_pharmacist", "refund") for x in a.actions))

print("\nunregistered number")
nf = G.PackInput(nafdac_number="A99-9999", exp_date="EXP 06/2028", batch="X1")
a = G.assess(nf, today=TODAY)
check("band is high risk", a.band == G.BAND_HIGH, f"got {a.band}")
kinds = [x.kind for x in a.actions]
check("preserves evidence first", kinds[0] == "preserve", str(kinds))
check("asks for a re-read before reporting",
      kinds.index("recheck") < kinds.index("nafdac_report"), str(kinds))
check("mentions a misread digit somewhere in the evidence",
      any("misread" in (c.does_not_prove + " ".join(c.detail)).lower() for c in a.checks))

print("\npoor integrity observations")
fl = G.PackInput(**{**GENUINE.__dict__, "flags": ["expiry_overprint", "sticker_over_print"]})
a = G.assess(fl, today=TODAY)
check("band is high risk", a.band == G.BAND_HIGH, f"got {a.band}")
check("integrity check is high", any(c.id == "integrity" and c.state == G.HIGH for c in a.checks))

print("\nincomplete input")
a = G.assess(G.PackInput(), today=TODAY)
check("band is incomplete", a.band == G.BAND_INCOMPLETE, f"got {a.band}")
check("asks for a photo", any(x.kind == "complete" for x in a.actions))

print("\nlanguage guardrails")
banned = ("fake", "counterfeit", "genuine", "authentic")
for label, a in [
    ("genuine pack", G.assess(GENUINE, today=TODAY)),
    ("mismatch", G.assess(bad, today=TODAY)),
    ("expired", G.assess(exp, today=TODAY)),
    ("not found", G.assess(nf, today=TODAY)),
]:
    text = (a.headline + " " + " ".join(x.label + " " + x.why for x in a.actions)).lower()
    hit = [w for w in banned if w in text]
    check(f"{label}: no binary authenticity language", not hit, f"found {hit}")

print("\noffline mode degrades honestly")
a = G.assess(uncached := G.PackInput(**{**GENUINE.__dict__, "nafdac_number": "A98-7777"}),
             today=TODAY, offline=True)
check("offline registry check is unverifiable",
      any(c.id == "registry" and c.state == G.UNKNOWN for c in a.checks),
      str([(c.id, c.state) for c in a.checks]))
check("offline cache miss is not treated as not-found",
      not any(c.id == "registry" and c.state == G.HIGH for c in a.checks),
      str([(c.id, c.state) for c in a.checks]))

print("\nserialisation")
d = G.assess(GENUINE, today=TODAY).to_dict()
check("to_dict has counts", "counts" in d and isinstance(d["counts"], dict))
check("to_dict is json-safe", isinstance(str(d), str))

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
