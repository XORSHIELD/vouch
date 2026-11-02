"""
GS1 element-string tests: DataMatrix payloads from traceability packs.

No network: parsing is pure string work.

Run:  python test_gs1.py
"""

from __future__ import annotations

import gs1

fails = 0

GTIN = "06260001146105"


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("human-readable form")
p = gs1.parse(f"(01){GTIN}(17)281231(10)LOT42(21)SN9")
check("gtin extracted", p["gtin"] == GTIN, repr(p["gtin"]))
check("gtin check passes", p["gtin_valid"] is True,
      repr(p["issues"]))
check("batch extracted", p["batch"] == "LOT42", repr(p["batch"]))
check("serial extracted", p["serial"] == "SN9", repr(p["serial"]))
check("expiry to ISO", p["expiry"] == "2028-12-31", repr(p["expiry"]))
check("no issues", p["issues"] == [], repr(p["issues"]))

print("transmitted form")
p = gs1.parse("]C1" + f"01{GTIN}" + "17281231" + "10LOT42\x1d21SN9")
check("symbology stripped, all fields",
      (p["gtin"], p["batch"], p["serial"], p["expiry"])
      == (GTIN, "LOT42", "SN9", "2028-12-31"), repr(p))
p = gs1.parse(f"01{GTIN}" + "17281231" + "10LOT42")
check("unterminated last field ok",
      p["batch"] == "LOT42" and p["serial"] == "", repr(p))

print("dates")
p = gs1.parse(f"(01){GTIN}(17)290200")
check("day 00 is month end", p["expiry"] == "2029-02-28",
      repr(p["expiry"]))
p = gs1.parse(f"(01){GTIN}(17)280230")
check("impossible day flagged",
      p["expiry"] == "" and any("day" in i for i in p["issues"]),
      repr(p))
p = gs1.parse(f"(01){GTIN}(17)289913")
check("impossible month flagged",
      p["expiry"] == "" and any("month" in i for i in p["issues"]),
      repr(p))
p = gs1.parse(f"(01){GTIN}(11)250115")
check("production date parsed", p["production"] == "2025-01-15",
      repr(p["production"]))

print("restraint")
p = gs1.parse("(01)06260001146107(17)281231")
check("bad check digit fails cleanly",
      p["gtin_valid"] is False
      and any("check digit" in i for i in p["issues"]), repr(p))
check("fields still returned despite bad digit",
      p["expiry"] == "2028-12-31", repr(p["expiry"]))
p = gs1.parse("(01)123(17)281231")
check("short GTIN unverified, not failed",
      p["gtin_valid"] is None and any("14 digits" in i for i in p["issues"]),
      repr(p))
p = gs1.parse("01" + GTIN + "99ABC")
check("unknown AI stops with an issue",
      p["gtin"] == GTIN and any("99" in i for i in p["issues"]),
      repr(p))
p = gs1.parse("(01)0626000114610")
check("truncated human form flagged",
      p["gtin_valid"] is None, repr(p))
p = gs1.parse("")
check("empty is an issue, not a crash",
      p["ais"] == {} and len(p["issues"]) == 1, repr(p))
p = gs1.parse("hello world")
check("prose is not a code",
      p["ais"] == {} and bool(p["issues"]), repr(p))

print("detection + summary")
check("detects human form", gs1.is_gs1(f"(01){GTIN}(17)281231"))
check("detects symbology form",
      gs1.is_gs1("]C101" + GTIN + "17281231"))
check("detects bare transmitted form",
      gs1.is_gs1("01" + GTIN + "17281231"))
check("rejects prose", not gs1.is_gs1("hello world"))
check("rejects short digits", not gs1.is_gs1("12345"))
s = gs1.summarize(gs1.parse(f"(01){GTIN}(17)281231(10)LOT42"))
check("summary names fields",
      GTIN in s and "LOT42" in s and "2028-12-31" in s, s)
s = gs1.summarize(gs1.parse("hello"))
check("unreadable summary honest", "Unreadable" in s, s)

print("endpoint")
import server as _server

r = _server.gs1_decode(_server.Gs1Body(payload=f"(01){GTIN}(17)281231"))
check("endpoint decodes",
      r["gtin"] == GTIN and r["expiry"] == "2028-12-31", repr(r)[:160])

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
