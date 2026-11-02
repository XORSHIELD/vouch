"""
Vouch — GS1 element-string parser.

NAFDAC's traceability rules put a GS1 2D DataMatrix on pharmaceutical
packs: one scan carrying the GTIN, batch, expiry and serial as
Application Identifiers (AIs 01/10/17/21). A camera or scanner hands us
the decoded string; this module turns it into named fields.

Two spellings, same meaning:
  human-readable  (01)06260001146100(17)281231(10)LOT42(21)SN9
  transmitted     01062600011461001728123110LOT42<GS>21SN9

Only the traceability AIs are interpreted (01, 10, 11, 17, 21).
Anything else stops the parse with an issue, never a guess. Dates use
the GS1 century rule (2000-based) and day 00 means the last day of
the month. stdlib only.
"""

from __future__ import annotations

import calendar
import re

import barcode as B

GS = "\x1d"
_SYMBOLOGY = re.compile(r"^\](C1|d2|Q3|e0|J1)")
_HUMAN_AI = re.compile(r"\((\d{2,4})\)")

# AI -> fixed length, or "var" for FNC1-terminated variable length.
FIXED = {"01": 14, "11": 6, "17": 6}
VARIABLE = {"10", "21"}
KNOWN = set(FIXED) | VARIABLE


def is_gs1(payload: str) -> bool:
    """Does this look like a GS1 element string at all?"""
    text = (payload or "").strip()
    if not text:
        return False
    if _SYMBOLOGY.match(text) or "(01)" in text[:8]:
        return True
    head = re.sub(r"\D", "", text[:16])
    return head.startswith("01") and len(head) >= 16


def _clean(payload: str) -> str:
    text = (payload or "").strip()
    text = _SYMBOLOGY.sub("", text).strip()
    return text


def _split_human(text: str) -> list[tuple[str, str]] | None:
    """(AI, value) pairs from the parenthesised spelling. None if absent."""
    if "(" not in text:
        return None
    parts = _HUMAN_AI.split(text)
    if len(parts) < 3 or parts[0].strip():
        return None
    out: list[tuple[str, str]] = []
    for i in range(1, len(parts) - 1, 2):
        out.append((parts[i], parts[i + 1].strip()))
    return out or None


def _split_transmitted(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """(AI, value) pairs from the FNC1 spelling, plus issues."""
    pairs: list[tuple[str, str]] = []
    issues: list[str] = []
    text = text.replace("\x1e", GS)
    pos = 0
    while pos < len(text):
        ai = text[pos:pos + 2]
        if ai not in KNOWN:
            issues.append(
                f"Stopped at '{ai or 'end'}': not a traceability AI "
                "(01, 10, 11, 17, 21).")
            break
        pos += 2
        if ai in FIXED:
            want = FIXED[ai]
            value = text[pos:pos + want]
            if len(value) < want:
                issues.append(f"AI {ai} is truncated "
                              f"({len(value)} of {want} characters).")
                break
            pos += want
        else:
            end = text.find(GS, pos)
            value = text[pos:] if end < 0 else text[pos:end]
            pos = len(text) if end < 0 else end + 1
            if not value:
                issues.append(f"AI {ai} has no value.")
                break
            if len(value) > 20:
                issues.append(f"AI {ai} is longer than 20 characters.")
        pairs.append((ai, value))
    return pairs, issues


def _expiry(raw: str) -> tuple[str, str]:
    """YYMMDD to ISO date. Day 00 is the last day of the month."""
    if not re.fullmatch(r"\d{6}", raw or ""):
        return "", f"Expiry '{raw}' is not YYMMDD."
    year, month, day = 2000 + int(raw[:2]), int(raw[2:4]), int(raw[4:6])
    if not 1 <= month <= 12:
        return "", f"Expiry month {month:02d} is not a month."
    last = calendar.monthrange(year, month)[1]
    if day == 0:
        return f"{year:04d}-{month:02d}-{last:02d}", ""
    if day > last:
        return "", f"Expiry day {day:02d} is past the end of the month."
    return f"{year:04d}-{month:02d}-{day:02d}", ""


def parse(payload: str) -> dict:
    """Parse a GS1 element string into traceability fields. Never raises."""
    out = {"gtin": "", "gtin_valid": None, "batch": "", "serial": "",
           "expiry": "", "production": "", "ais": {}, "issues": []}
    try:
        return _parse((payload or "").strip(), out)
    except Exception as exc:
        out["issues"].append(f"Could not parse this code ({exc}).")
        return out


def _parse(text: str, out: dict) -> dict:
    if not text:
        out["issues"].append("No code given.")
        return out
    text = _clean(text)
    pairs = _split_human(text)
    if pairs is None:
        pairs, out["issues"] = _split_transmitted(text)
    seen: set[str] = set()
    for ai, value in pairs:
        if ai not in KNOWN:
            out["issues"].append(f"Ignored AI {ai}: not a traceability AI.")
            continue
        if ai in seen:
            out["issues"].append(f"AI {ai} appears twice; kept the first.")
            continue
        seen.add(ai)
        out["ais"][ai] = value
    if not out["ais"]:
        if not out["issues"]:
            out["issues"].append("No traceability fields found.")
        return out
    gtin = out["ais"].get("01", "")
    if gtin:
        out["gtin"] = gtin
        valid = (B.checksum_ok(gtin)
                 if (gtin.isdigit() and len(gtin) == 14) else None)
        out["gtin_valid"] = valid
        if valid is False:
            out["issues"].append(
                "The GTIN check digit fails; a digit was misread.")
        elif valid is None:
            out["issues"].append("The GTIN is not 14 digits.")
    for ai, key in (("17", "expiry"), ("11", "production")):
        if ai in out["ais"]:
            iso, problem = _expiry(out["ais"][ai])
            if problem:
                out["issues"].append(problem)
            else:
                out[key] = iso
    out["batch"] = out["ais"].get("10", "")
    out["serial"] = out["ais"].get("21", "")
    return out


def summarize(parsed: dict) -> str:
    """One short line about a parsed code, for SMS and chat."""
    bits = []
    if parsed.get("gtin"):
        mark = ("check digit ok" if parsed.get("gtin_valid") is True
                else "check digit FAILED" if parsed.get("gtin_valid") is False
                else "unverified length")
        bits.append(f"GTIN {parsed['gtin']} ({mark})")
    if parsed.get("batch"):
        bits.append(f"batch {parsed['batch']}")
    if parsed.get("expiry"):
        bits.append(f"expiry {parsed['expiry']}")
    if parsed.get("serial"):
        bits.append(f"serial {parsed['serial']}")
    if not bits:
        problems = parsed.get("issues") or ["nothing readable"]
        return "Unreadable GS1 code: " + problems[0]
    text = "; ".join(bits) + "."
    if parsed.get("issues"):
        text += " Note: " + parsed["issues"][0]
    return text
