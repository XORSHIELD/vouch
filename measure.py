"""
Measure the registry's own numbering inconsistency.

This produces the headline number for the pitch: what fraction of official
records cannot be found by a naive exact match on their alternate spelling.

Run:  python measure.py [samples] [page]
"""

from __future__ import annotations

import json
import random
import re
import sys

import registry as R


def classify(form: str) -> str:
    if not form:
        return "empty"
    if re.fullmatch(r"[A-Z]\d{1,2}-\d{4}", form):
        return "alpha-4digit"
    if re.fullmatch(r"[A-Z]\d{1,2}-\d{6}", form):
        return "alpha-6digit"
    if re.fullmatch(r"\d{1,2}-\d{4}", form):
        return "numeric-4digit"
    if re.fullmatch(r"\d{1,2}-\d{6}", form):
        return "numeric-6digit"
    return "other"


def main() -> int:
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    total = R.search("", length=1)["recordsTotal"]
    print(f"registry total: {total} records\n")

    rng = random.Random(7)
    max_start = max(0, total - size)
    starts = [rng.randrange(0, max_start) for _ in range(pages)]

    rows, seen = [], set()
    for s in starts:
        payload = R.search("", length=size, start=s)
        for d in payload.get("data") or []:
            rec = R.Record.from_api(d)
            if rec.nafdac and rec.nafdac not in seen:
                seen.add(rec.nafdac)
                rows.append(rec)

    print(f"sampled {len(rows)} distinct records from {pages} pages\n")

    forms: dict[str, int] = {}
    status: dict[str, int] = {}
    for r in rows:
        forms[classify(r.nafdac)] = forms.get(classify(r.nafdac), 0) + 1
        status[r.status] = status.get(r.status, 0) + 1

    print("number formats")
    for k, v in sorted(forms.items(), key=lambda x: -x[1]):
        print(f"  {k:<14} {v:>5}  {100*v/len(rows):5.1f}%")

    print("\nstatus")
    for k, v in sorted(status.items(), key=lambda x: -x[1]):
        print(f"  {k:<14} {v:>5}  {100*v/len(rows):5.1f}%")

    # The real risk metric: how often does the DISJOINT series hold a
    # different product at the same tail? That is the false-positive exposure
    # of any implementation that "tries the other spelling".
    print("\ndisjoint-series collision exposure (live, cached)")
    exposure, checked = [], 0
    for r in rows:
        alt = R.explore_variants(r.nafdac)
        if not alt:
            continue
        checked += 1
        if checked > 40:
            break
        payload = R.search(alt[0], length=6)
        other = [
            R.Record.from_api(d) for d in (payload.get("data") or [])
            if alt[0] in R.normalise(R.Record.from_api(d).nafdac)
        ]
        if other and other[0].product_name.strip().lower() != r.product_name.strip().lower():
            exposure.append((r.nafdac, r.product_name, alt[0], other[0].product_name))
    print(f"  checked {checked} records")
    print(f"  disjoint spelling exists as a DIFFERENT product: {len(exposure)}")
    for e in exposure[:10]:
        print(f"    {e[0]:<11} {e[1][:30]:<30} vs {e[2]:<11} {e[3][:30]}")

    share = 100 * (forms.get("numeric-4digit", 0) + forms.get("numeric-6digit", 0)) / len(rows)
    print(f"\n{share:.1f}% of sampled records use the numeric spelling "
          f"(a separate series, not a synonym).")
    print(json.dumps({"sample": len(rows), "numeric_share": round(share, 1),
                      "collisions": exposure[:20]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
