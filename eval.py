"""
Vouch — evaluation.

Three questions, measured separately, because they fail in different ways:

  1. CALIBRATION. Does the band match what a careful person would say?
     Over-calling is not a safe default here: telling someone their medicine is
     high-risk when it is fine has a cost, and it is the failure mode that makes
     verification tools untrustworthy.

  2. STABILITY UNDER OCR NOISE. A single misread character must not turn a
     clean pack into an accusation. This is the most important measurement in
     the whole project.

  3. REGISTRY AUDIT. How much of the official register can this tool actually
     resolve, and where does its numbering break?

Run:
    python eval.py            calibration + stability
    python eval.py --full     also audits the full registry (network, ~1 min)
    python eval.py --offline  use only the local cache

Exit code is 1 if any labelled case is out of range, so this can gate a release.
"""

from __future__ import annotations

import random
import re
import sys
from datetime import date, timedelta

import engine as G
import expiry as E
import registry as R

FAIL = 0
TODAY = date.today()


def _exp(days: int) -> str:
    d = TODAY + timedelta(days=days)
    return f"EXP {d.month:02d}/{d.year}"


# --------------------------------------------------------------------------
# 1. Calibration set
# --------------------------------------------------------------------------
#
# `expect` is the set of acceptable bands. Where two answers are genuinely
# defensible, both are listed — the point is to catch wrong answers, not to
# punish a reasonable reading.

CASES: list[dict] = [
    {
        "name": "genuine pack, everything reconciles",
        "expect": {"clear"},
        "pack": dict(nafdac_number="A11-0551", manufacturer="A.C. Drugs Ltd",
                     product_name="AC-Drex Tablet", batch="ACD2401",
                     exp_date="EXP 06/2028", mfg_date="MFG 06/2025"),
    },
    {
        "name": "genuine pack, number printed with spaces and lowercase",
        "expect": {"clear"},
        "pack": dict(nafdac_number="a11 0551", manufacturer="A.C. Drugs Ltd",
                     product_name="AC-Drex Tablet", batch="ACD2401",
                     exp_date="EXP 06/2028"),
    },
    {
        "name": "genuine pack, number read with OCR noise (O for 0, I for 1)",
        "expect": {"clear"},
        "pack": dict(nafdac_number="A1I-O551", manufacturer="A.C. Drugs Ltd",
                     product_name="AC-Drex Tablet", batch="ACD2401",
                     exp_date="EXP 06/2028"),
    },
    {
        "name": "genuine pack, expiry printed as a full date",
        "expect": {"clear"},
        "pack": dict(nafdac_number="A11-0551", manufacturer="A.C. Drugs Ltd",
                     product_name="AC-Drex Tablet", batch="ACD2401",
                     exp_date="30/06/2028"),
    },
    {
        "name": "genuine pack, expiry with two-digit year",
        "expect": {"clear"},
        "pack": dict(nafdac_number="A11-0551", manufacturer="A.C. Drugs Ltd",
                     product_name="AC-Drex Tablet", batch="ACD2401",
                     exp_date="EXP 06/28"),
    },
    {
        "name": "the number is real, the company is not",
        "expect": {"high_risk"},
        "pack": dict(nafdac_number="A11-0551",
                     manufacturer="Emzor Pharmaceutical Industries Ltd",
                     product_name="AC-Drex Tablet", batch="EMZ7781",
                     exp_date="EXP 09/2027", mfg_date="MFG 09/2024"),
    },
    {
        "name": "expired stock on the shelf",
        "expect": {"high_risk"},
        "pack": dict(nafdac_number="A11-0551", manufacturer="A.C. Drugs Ltd",
                     product_name="AC-Drex Tablet", batch="ACD1902",
                     exp_date="EXP 01/2026", mfg_date="MFG 01/2023"),
        "flags": ["expiry_overprint"],
    },
    {
        "name": "expires within two months",
        "expect": {"review"},
        "pack": dict(nafdac_number="A11-0551", manufacturer="A.C. Drugs Ltd",
                     product_name="AC-Drex Tablet", batch="ACD2401",
                     exp_date=_exp(45)),
    },
    {
        "name": "number that exists nowhere",
        "expect": {"high_risk"},
        "pack": dict(nafdac_number="A99-9999",
                     manufacturer="Global Health Labs",
                     product_name="Malaria Guard Plus", batch="GH-0091",
                     exp_date="EXP 11/2027"),
        "flags": ["misspelling", "poor_print"],
    },
    {
        "name": "registration lapsed, product was real",
        "expect": {"review", "high_risk"},
        "pack": dict(nafdac_number="B4-0262",
                     manufacturer="Novartis Nigeria Limited",
                     product_name="Coartem 80/480 Tablets", batch="F4451",
                     exp_date="EXP 01/2027"),
    },
    {
        "name": "pack outlives its registration window",
        "expect": {"review", "high_risk"},
        "pack": dict(nafdac_number="A11-0551", manufacturer="A.C. Drugs Ltd",
                     product_name="AC-Drex Tablet", batch="ACD2507",
                     exp_date="EXP 01/2030"),
    },
    {
        "name": "nothing legible",
        "expect": {"incomplete"},
        "pack": dict(),
    },
    {
        "name": "expiry legible, no NAFDAC number",
        "expect": {"review", "high_risk"},
        "pack": dict(manufacturer="A.C. Drugs Ltd",
                     product_name="AC-Drex Tablet", batch="ACD2401",
                     exp_date="EXP 06/2028"),
    },
    {
        "name": "expiry missing, registration fine",
        "expect": {"review", "high_risk"},
        "pack": dict(nafdac_number="A11-0551", manufacturer="A.C. Drugs Ltd",
                     product_name="AC-Drex Tablet", batch="ACD2401"),
    },
    {
        "name": "batch is a lazy placeholder",
        "expect": {"review", "high_risk"},
        "pack": dict(nafdac_number="A11-0551", manufacturer="A.C. Drugs Ltd",
                     product_name="AC-Drex Tablet", batch="0000",
                     exp_date="EXP 06/2028"),
    },
    {
        "name": "manufacture date after expiry date",
        "expect": {"high_risk"},
        "pack": dict(nafdac_number="A11-0551", manufacturer="A.C. Drugs Ltd",
                     product_name="AC-Drex Tablet", batch="ACD2401",
                     mfg_date="MFG 06/2028", exp_date="EXP 03/2026"),
    },
]


def run_case(case: dict, offline: bool = False) -> G.Assessment:
    p = G.PackInput(**case["pack"])
    p.flags = case.get("flags", [])
    return G.assess(p, today=TODAY, offline=offline)


def calibration(offline: bool = False) -> None:
    global FAIL
    print(f"1. VERDICT CALIBRATION — {len(CASES)} labelled packs\n")
    in_range = 0
    severe = 0
    lenient = 0
    severity = {"clear": 0, "incomplete": 1, "review": 2, "high_risk": 3}

    for case in CASES:
        a = run_case(case, offline)
        expect = case["expect"]
        ok = a.band in expect
        if ok:
            in_range += 1
            mark = "ok  "
        else:
            FAIL += 1
            want = "/".join(sorted(expect, key=lambda b: severity[b]))
            if severity.get(a.band, 0) > max(severity[b] for b in expect):
                severe += 1
                mark = "OVER"
            else:
                lenient += 1
                mark = "UNDER"
        print(f"  {mark}  {case['name']}")
        if not ok:
            print(f"        expected {sorted(expect)}, got {a.band}")
            print(f"        {a.headline}")
            for c in a.checks:
                if c.state in ("attention", "high_risk"):
                    print(f"          [{c.state}] {c.label}: {c.evidence}")

    n = len(CASES)
    print(f"\n  in range        {in_range}/{n}  ({in_range / n:.1%})")
    print(f"  over-called     {severe}")
    print(f"  under-called    {lenient}\n")


# --------------------------------------------------------------------------
# 2. Stability under OCR noise
# --------------------------------------------------------------------------

MUTATIONS = [
    ("O/0", lambda s: s.replace("0", "O", 1)),
    ("I/1", lambda s: s.replace("1", "I", 1)),
    ("l/1", lambda s: s.replace("1", "l", 1)),
    ("S/5", lambda s: s.replace("5", "S", 1)),
    ("B/8", lambda s: s.replace("8", "B", 1)),
    ("lowercase", lambda s: s.lower()),
    ("spaces", lambda s: s.replace("-", " ").replace(" ", "  ")),
    ("dots", lambda s: s.replace("-", ".")),
    ("no hyphen", lambda s: s.replace("-", "")),
    ("double hyphen", lambda s: s.replace("-", "--")),
]


def stability(offline: bool = False) -> None:
    global FAIL
    print("2. STABILITY UNDER OCR NOISE\n")
    print("   A misread character must not turn a clean pack into an accusation.\n")

    baseline_packs = [
        ("A11-0551", "A.C. Drugs Ltd", "AC-Drex Tablet"),
        ("B4-0262", "Novartis Nigeria Limited", "Coartem 80/480 Tablets"),
        ("A99-9999", "Global Health Labs", "Malaria Guard Plus"),
    ]

    trials = 0
    flips = 0
    false_accusations = 0

    for number, maker, product in baseline_packs:
        base = G.assess(G.PackInput(
            nafdac_number=number, manufacturer=maker, product_name=product,
            batch="ACD2401", exp_date="EXP 06/2028"), today=TODAY, offline=offline)
        print(f"   {number:10} baseline {base.band}")
        for label, fn in MUTATIONS:
            mutated = fn(number)
            if mutated == number:
                continue
            a = G.assess(G.PackInput(
                nafdac_number=mutated, manufacturer=maker,
                product_name=product, batch="ACD2401",
                exp_date="EXP 06/2028"), today=TODAY, offline=offline)
            trials += 1
            if a.band != base.band:
                flips += 1
                if base.band == "clear" and a.band == "high_risk":
                    false_accusations += 1
                print(f"      {label:12} {mutated:12} -> {a.band}"
                      f"   (baseline {base.band})")
    print(f"\n   mutations tried       {trials}")
    print(f"   verdict changed       {flips}")
    print(f"   false accusations     {false_accusations}")
    if false_accusations:
        FAIL += 1
        print("   FAIL: a misread character produced a high-risk verdict")
    print()


# --------------------------------------------------------------------------
# 3. Registry audit
# --------------------------------------------------------------------------

def _classify(form: str) -> str:
    if not form:
        return "empty"
    if re.fullmatch(r"[A-Z]\d{1,2}-\d{4}", form):
        return "letter + 4 digits"
    if re.fullmatch(r"[A-Z]\d{1,2}-\d{6}", form):
        return "letter + 6 digits"
    if re.fullmatch(r"\d{1,2}-\d{4}", form):
        return "numeric, 4 digits"
    if re.fullmatch(r"\d{1,2}-\d{6}", form):
        return "numeric, 6 digits"
    return "other"


def registry_audit(size: int = 1000) -> None:
    print("3. REGISTRY AUDIT\n")
    try:
        total = R.search("", length=1)["recordsTotal"]
    except Exception as exc:
        print(f"   registry unreachable: {exc}\n")
        return

    records: list[R.Record] = []
    start = 0
    while start < total:
        payload = R.search("", start=start, length=size)
        data = payload.get("data") or []
        if not data:
            break
        for d in data:
            try:
                records.append(R.Record.from_api(d))
            except Exception:
                pass
        start += len(data)

    print(f"   records fetched   {len(records)} of {total}")

    formats: dict[str, int] = {}
    statuses: dict[str, int] = {}
    canonical: dict[str, set[str]] = {}
    unresolved = 0

    for r in records:
        formats[_classify(r.nafdac)] = formats.get(_classify(r.nafdac), 0) + 1
        statuses[r.status or "unknown"] = statuses.get(r.status or "unknown", 0) + 1
        forms = R.normalise(r.nafdac)
        if not forms:
            unresolved += 1
            continue
        canonical.setdefault(forms[0], set()).add(r.product_name or "")

    print(f"   cannot normalise   {unresolved}")
    print("\n   numbering formats")
    for k, v in sorted(formats.items(), key=lambda x: -x[1]):
        print(f"     {k:22} {v:5}  ({v / max(1, len(records)):.1%})")

    print("\n   registration status")
    for k, v in sorted(statuses.items(), key=lambda x: -x[1]):
        print(f"     {k:22} {v:5}  ({v / max(1, len(records)):.1%})")

    collisions = {k: v for k, v in canonical.items()
                  if len({p for p in v if p}) > 1}
    print(f"\n   canonical-form collisions between different products: "
          f"{len(collisions)}")
    for k, v in list(collisions.items())[:5]:
        print(f"     {k}: {len(v)} products")

    # A random sample of live lookups: does the tool actually retrieve the
    # record for a number it was handed?
    rnd = random.Random(11)
    sample = rnd.sample(records, min(25, len(records)))
    hits = 0
    for r in sample:
        try:
            res = R.lookup(r.nafdac)
        except Exception:
            continue
        if res.matched and res.matched.nafdac.upper() == r.nafdac.upper():
            hits += 1
    print(f"   live lookup of a random sample: {hits}/{len(sample)} returned "
          f"the same record\n")


def main() -> int:
    args = set(sys.argv[1:])
    offline = "--offline" in args
    calibration(offline)
    stability(offline)
    if "--full" in args:
        registry_audit()
    print(f"{'FAILED' if FAIL else 'all checks passed'} ({FAIL} failures)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
