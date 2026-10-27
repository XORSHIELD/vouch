"""Tests for the expiry parser. Run: python test_expiry.py"""

from __future__ import annotations

from datetime import date

import expiry as E

TODAY = date(2026, 10, 27)
fails = 0


def eq(label: str, got, want) -> None:
    global fails
    if got != want:
        fails += 1
        print(f"  FAIL  {label}\n        got  {got!r}\n        want {want!r}")


def yes(label: str, cond: bool, detail: str = "") -> None:
    global fails
    if not cond:
        fails += 1
        print(f"  FAIL  {label}  {detail}")


print("parsing shapes")
eq("EXP 03/2027", (E.parse_date("EXP 03/2027").year, E.parse_date("EXP 03/2027").month), (2027, 3))
eq("03/27", (E.parse_date("EXP 03/27").year, E.parse_date("EXP 03/27").month), (2027, 3))
eq("MAR 2027", (E.parse_date("EXP MAR 2027").year, E.parse_date("EXP MAR 2027").month), (2027, 3))
eq("2027-03-15", (E.parse_date("2027-03-15").year, E.parse_date("2027-03-15").day), (2027, 15))
eq("15 MAR 2027", (E.parse_date("15 MAR 2027").month, E.parse_date("15 MAR 2027").day), (3, 15))
eq("bare year", E.parse_date("2027").precision, "year")
eq("garbage", E.parse_date("BATCH 99X"), "BATCH 99X" and E.parse_date("BATCH 99X"))
yes("nothing parses from junk", E.parse_date("XYZ") is None, str(E.parse_date("XYZ")))

print("\nexpiry states")
a = E.assess_expiry("EXP 02/2026", today=TODAY)
eq("past date is expired", a.state, E.EXPIRED)

a = E.assess_expiry("EXP 03/2026", today=TODAY)
eq("month already passed is expired", a.state, E.EXPIRED)

a = E.assess_expiry("EXP 10/2026", today=TODAY)
eq("current month is still valid (end of month rule)", a.state, E.EXPIRING)

a = E.assess_expiry("EXP 12/2026", today=TODAY)
eq("two months out is expiring", a.state, E.EXPIRING)

a = E.assess_expiry("EXP 06/2028", today=TODAY)
eq("far future is valid", a.state, E.VALID)
yes("valid has positive days", (a.days_left or 0) > 90)

print("\nconservative bias")
# Only a month is known: we must not call it expired on the 1st of that month.
a = E.assess_expiry("EXP 10/2026", today=date(2026, 10, 1))
eq("first of expiry month is not expired", a.state, E.EXPIRING)

print("\nimplausible and inconsistent input")
a = E.assess_expiry("EXP 03/1998", today=TODAY)
eq("ancient year is implausible", a.state, E.IMPLAUSIBLE)
a = E.assess_expiry("EXP 03/2099", today=TODAY)
eq("far-future year is implausible", a.state, E.IMPLAUSIBLE)

a = E.assess_expiry("EXP 03/2025", "MFG 06/2026", today=TODAY)
eq("made after it expired is a sequence error", a.state, E.SEQUENCE_ERROR)

a = E.assess_expiry("", today=TODAY)
eq("missing expiry is unreadable", a.state, E.UNREADABLE)

print("\nlabelled fields")
a = E.assess_expiry("MFG 01/2024 EXP 01/2027", today=TODAY)
eq("labelled exp parsed from a combined string", a.state, E.VALID)
eq("labelled exp month", a.exp.month, 1)

a = E.assess_expiry("BEST BEFORE 09/2027", today=TODAY)
eq("best-before parsed", a.state, E.VALID)

print("\nunusual shelf life is a signal, not a failure")
a = E.assess_expiry("EXP 01/2035", "MFG 01/2024", today=TODAY)
yes("11-year shelf life raises a signal", any("shelf life" in s for s in a.signals),
    str(a.signals))

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
