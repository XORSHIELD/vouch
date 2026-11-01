"""
Batch-code manufacture-date decoding (pilot).

Two verified algorithms only — Beiersdorf year+week, P&G year+julian —
each corroborated by two independent public sources. Open-dated brands
get guidance; everything else answers "not verified" instead of guessing.

Run:  python test_batchdecode.py
"""

from __future__ import annotations

from datetime import date

import batchdecode as B

fails = 0
TODAY = date(2026, 11, 1)


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("beiersdorf (nivea)")
d = B.decode("Nivea Soft", "81535542", today=TODAY)
check("nivea week code decodes",
      d.confidence == "decoded" and d.made == date(2018, 4, 9)
      and d.precision == "week", repr(d))
check("decade ambiguity disclosed",
      2008 in d.alt_years, repr(d.alt_years))
d = B.decode("nivea", "81535542AB", today=TODAY)
check("trailing plant letters accepted", d.confidence == "decoded"
      and d.made == date(2018, 4, 9), repr(d))
d = B.decode("Nivea", "85595542", today=TODAY)
check("impossible week refused", d.confidence == "unknown", repr(d))

print("p&g (olay, h&s, oral-b, gillette, ariel, safeguard)")
d = B.decode("Olay", "53450386BA", today=TODAY)
check("p&g julian code decodes",
      d.confidence == "decoded" and d.made == date(2025, 12, 11)
      and d.precision == "day", repr(d))
check("older decades listed",
      d.alt_years == [2015, 2005, 1995], repr(d.alt_years))
d = B.decode("Head & Shoulders", "53450386BA", today=TODAY)
check("h&s shares the p&g rule", d.confidence == "decoded", repr(d))
d = B.decode("Ariel", "1234", today=TODAY)
check("short code refused", d.confidence == "unknown", repr(d))

print("loreal group (garnier, maybelline, ...)")
d = B.decode("Garnier", "38S60OS", today=TODAY)
check("garnier 7-char code decodes",
      d.confidence == "decoded" and d.made == date(2019, 6, 1)
      and d.precision == "month", repr(d))
check("22-year cycle disclosed",
      d.alt_years == [1997], repr(d.alt_years))
d = B.decode("Kiehl's", "28Y801", today=TODAY)
check("documented kiehl's example matches",
      d.confidence == "decoded" and d.made == date(2024, 8, 1),
      repr(d))
d = B.decode("Maybelline", "22U300T", today=TODAY)
check("dated forum example matches",
      d.confidence == "decoded" and d.made == date(2021, 3, 1),
      repr(d))
d = B.decode("L'Oreal Paris", "91TN00K", today=TODAY)
check("letter month N is november",
      d.confidence == "decoded" and d.made == date(2020, 11, 1),
      repr(d))
d = B.decode("Garnier", "SGT20WA", today=TODAY)
check("letter-led plant accepted",
      d.confidence == "decoded" and d.made == date(2020, 2, 1),
      repr(d))
d = B.decode("Garnier", "CD126", today=TODAY)
check("old 5-char format decodes",
      d.confidence == "decoded" and d.made == date(2007, 5, 6)
      and d.precision == "day", repr(d))
d = B.decode("Garnier", "38Q60OS", today=TODAY)
check("skipped year letter refused", d.confidence == "unknown",
      repr(d))
d = B.decode("Dudu Osun", "38S60OS", today=TODAY)
check("right shape, wrong brand refused",
      d.confidence == "unknown", repr(d))

print("guidance brands")
d = B.decode("Dove soap", "12661", today=TODAY)
check("unilever guided, not decoded",
      d.confidence == "guidance" and d.made is None
      and "openly" in d.note, repr(d))
d = B.decode("Close-Up", "XJ37AA", today=TODAY)
check("close-up shares unilever guidance",
      d.confidence == "guidance", repr(d))
d = B.decode("Colgate", "anything", today=TODAY)
check("colgate guided", d.confidence == "guidance", repr(d))
d = B.decode("Dettol", "anything", today=TODAY)
check("dettol guided", d.confidence == "guidance", repr(d))

print("unknown brands")
d = B.decode("Dudu Osun", "ABC123", today=TODAY)
check("unverified brand honest",
      d.confidence == "unknown" and d.made is None
      and "Nivea" in d.note, repr(d))
d = B.decode("", "81535542", today=TODAY)
check("no brand, no decode", d.confidence == "unknown", repr(d))

print("endpoints")
import server
r = server.batch_decode("Nivea", "64010123")
check("decode endpoint answers",
      r["confidence"] == "decoded" and r["made_label"] == "Sep 2026"
      and 2016 in r["alt_years"], str(r)[:200])
g = server.batch_brands()
check("brand groups listed",
      any(x["id"] == "pg" and x["mode"] == "decode"
          for x in g["groups"])
      and any(x["id"] == "unilever" and x["mode"] == "guidance"
              for x in g["groups"]), str(g)[:200])

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
