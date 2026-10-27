"""
Collision test: are the alpha and numeric NAFDAC series two spellings of one
number, or two disjoint series?

This decides a design question with real consequences. If they are spellings,
the lookup must try both. If they are disjoint series, trying both can match
a completely different product and produce a false accusation against a
genuine pack.
"""

from __future__ import annotations

import random
import sys

import registry as R


def name_of(number: str) -> tuple[str, str] | None:
    payload = R.search(number, length=6)
    for d in payload.get("data") or []:
        rec = R.Record.from_api(d)
        if number in R.normalise(rec.nafdac) or rec.nafdac.upper() == number.upper():
            return rec.product_name, rec.manufacturer
    return None


def main() -> int:
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    size = 100
    total = R.search("", length=1)["recordsTotal"]
    rng = random.Random(11)
    starts = [rng.randrange(0, max(0, total - size)) for _ in range(pages)]

    rows, seen = [], set()
    for s in starts:
        for d in (R.search("", length=size, start=s).get("data") or []):
            rec = R.Record.from_api(d)
            if rec.nafdac and rec.nafdac not in seen:
                seen.add(rec.nafdac)
                rows.append(rec)

    numeric = [r for r in rows if r.nafdac[:2].isdigit()][:25]
    alpha = [r for r in rows if r.nafdac[:1].isalpha()][:25]

    collisions, found_variant, checked = [], [], 0
    for r in numeric + alpha:
        parts = r.nafdac.split("-")
        if len(parts) != 2:
            continue
        head, tail = parts
        if head.isdigit():
            alt = f"A{int(head)}-{tail}"
        elif head[0].isalpha() and head[1:].isdigit():
            alt = f"{int(head[1:]):02d}-{tail}"
        else:
            continue
        got = name_of(alt)
        checked += 1
        if got is None:
            continue
        found_variant.append((r.nafdac, r.product_name, alt, got[0]))
        if got[0] != r.product_name:
            collisions.append((r.nafdac, r.product_name, alt, got[0], got[1]))

    print(f"checked {checked} records\n")
    print(f"alternate spelling resolves at all : {len(found_variant)}")
    print(f"alternate resolves to a DIFFERENT product : {len(collisions)}\n")
    for c in found_variant[:15]:
        flag = "COLLISION" if c in collisions else "same"
        print(f"  {flag:<9} {c[0]:<11} {c[1][:34]:<34} -> {c[2]:<11} {c[3][:34]}")
    if collisions:
        print("\nCONCLUSION: the series are disjoint and can collide.")
        print("Cross-spelling fallback must never produce a REGISTERED or MISMATCH verdict.")
    else:
        print("\nCONCLUSION: alternates almost never resolve; treat the series as disjoint.")
        print("Cross-spelling fallback adds risk without adding recall.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
