"""
Regression tests for the Vouch registry layer.

These are the behaviours that must never regress, because each one is a
false accusation waiting to happen.

Run:  python test_registry.py
"""

from __future__ import annotations

import sys

import registry as R

CASES: list[tuple[str, str, dict]] = [
    # (label, printed number, kwargs)
    ("clean number resolves",            "A11-0551", {}),
    ("OCR letter O for zero",            "O3-0634",  {}),
    ("lowercase and spaces",             "a11 - 0551", {}),
    ("unregistered number",              "A99-9999", {}),
    ("lapsed registration",              "B4-0262",  {}),
]

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        failures += 1


def test_normalise() -> None:
    print("\nnormalise: never cross numbering series")
    # The forbidden transformation.
    for printed in ("A4-0271", "04-0271", "A4-7750", "04-7750"):
        c = R.normalise(printed)
        other_series = R.explore_variants(printed)
        check(
            f"{printed} keeps its series",
            other_series == [] or other_series[0] not in c,
            f"got {c}",
        )

    check("A11-0551 is stable", R.normalise("A11-0551")[0] == "A11-0551",
          str(R.normalise("A11-0551")))
    check("O3-0634 repairs to 03-0634", "03-0634" in R.normalise("O3-0634"),
          str(R.normalise("O3-0634")))
    check("AI1-0551 repairs to A11-0551", "A11-0551" in R.normalise("AI1-0551"),
          str(R.normalise("AI1-0551")))
    check("blank input is empty", R.normalise("") == [], str(R.normalise("")))

    # Display-only variant must still be able to describe the other series.
    check("explore_variants(A4-0271) -> 04-0271",
          R.explore_variants("A4-0271") == ["04-0271"],
          str(R.explore_variants("A4-0271")))


def test_lookup() -> None:
    print("\nlookup: verdicts against the live registry")
    expect = {
        "A11-0551": R.REGISTERED,
        "O3-0634": R.REGISTERED,
        "a11 - 0551": R.REGISTERED,
        "A99-9999": R.NOT_FOUND,
        "B4-0262": R.INACTIVE,
    }
    for printed, want in expect.items():
        res = R.lookup(printed)
        check(f"{printed} -> {want}", res.verdict == want,
              f"got {res.verdict}: {res.message}")

    # The demo moment: right number, wrong company printed on the pack.
    res = R.lookup("A11-0551", manufacturer_on_pack="Emzor Pharmaceutical Industries Ltd")
    check("copied number -> MISMATCH", res.verdict == R.MISMATCH,
          f"got {res.verdict}")
    check("copied number carries the registry holder",
          bool(res.matched and "A.C. Drugs" in res.matched.manufacturer))

    # A genuine manufacturer must NOT be flagged.
    res = R.lookup("A11-0551", manufacturer_on_pack="A.C. Drugs Ltd")
    check("correct manufacturer -> REGISTERED", res.verdict == R.REGISTERED,
          f"got {res.verdict}: {res.message}")

    res = R.lookup("A11-0551", manufacturer_on_pack="A C DRUGS LIMITED")
    check("manufacturer spacing/case tolerated -> REGISTERED",
          res.verdict == R.REGISTERED, f"got {res.verdict}")

    # Guardrail: no verdict may ever be a binary authenticity claim.
    for printed in ("A11-0551", "A99-9999", "B4-0262"):
        res = R.lookup(printed)
        check(f"{printed} verdict is a state, not a truth claim",
              res.verdict in {R.REGISTERED, R.MISMATCH, R.INACTIVE,
                              R.UNVERIFIABLE, R.NOT_FOUND, R.AMBIGUOUS})


def main() -> int:
    test_normalise()
    try:
        test_lookup()
    except R.RegistryUnavailable as exc:
        print(f"\nSKIP live tests: registry unavailable ({exc})")
        return 0
    print(f"\n{'FAILED' if failures else 'all checks passed'} ({failures} failures)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
