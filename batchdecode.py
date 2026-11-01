"""
Vouch — batch-code manufacture-date decoding (pilot).

Two verified algorithms, each corroborated by two independent public
sources (retailer Q&As quoting the makers, plus decoder references):

- Beiersdorf (Nivea): 8 digits, first = last digit of the year, next
  two = production week. Trailing plant letters allowed.
- P&G (Olay, Head & Shoulders, Oral-B, Gillette, Ariel, Safeguard):
  first digit = last digit of the year, next three = day of the year.

Brands whose makers print open dates (Unilever, Colgate, Reckitt) get
guidance to those dates. Everything else answers "not verified" —
a guessed decode would be an invented fact, and this module will not
produce one.

Single-digit years repeat every decade, so every decode carries all
plausible years, most recent first. Callers must show the ambiguity,
never just the top date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class Decode:
    confidence: str  # "decoded" | "guidance" | "unknown"
    group: str = ""
    brand_label: str = ""
    made: date | None = None
    precision: str = ""  # "week" | "day" | ""
    alt_years: list[int] = field(default_factory=list)
    method: str = ""
    note: str = ""

    @property
    def made_label(self) -> str:
        return self.made.strftime("%b %Y") if self.made else ""

    def age_years(self, today: date | None = None) -> float | None:
        if not self.made:
            return None
        today = today or date.today()
        return (today - self.made).days / 365.25

    def to_dict(self) -> dict:
        return {
            "confidence": self.confidence,
            "group": self.group,
            "brand": self.brand_label,
            "made": self.made.isoformat() if self.made else None,
            "made_label": self.made_label,
            "precision": self.precision,
            "alt_years": list(self.alt_years),
            "method": self.method,
            "note": self.note,
        }


def _norm(text: str) -> str:
    inner = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    return f" {re.sub(r'\\s+', ' ', inner)} "


_ALIASES = {
    "beiersdorf": ["nivea", "beiersdorf"],
    "pg": ["olay", "head shoulders", "head and shoulders", "oral b",
           "oralb", "gillette", "ariel", "safeguard", "pantene", "crest"],
    "unilever": ["dove", "lux", "vaseline", "close up", "closeup",
                 "rexona", "axe", "lifebuoy", "pepsodent"],
    "colgate": ["colgate", "palmolive"],
    "reckitt": ["dettol"],
}

_LABELS = {
    "beiersdorf": "Nivea",
    "pg": "P&G",
    "unilever": "Unilever",
    "colgate": "Colgate",
    "reckitt": "Reckitt",
}

_GUIDANCE = {
    "unilever": ("Unilever packs (Dove, Lux, Vaseline, Close-Up and kin) "
                 "usually print the manufacture and best-before dates "
                 "openly near the crimp or base — read those instead of "
                 "decoding. There is no verified public decoder for "
                 "Unilever batch codes."),
    "colgate": ("Colgate prints the manufacture and expiry dates openly "
                "on the tube crimp — read those. There is no verified "
                "public decoder for Colgate batch codes."),
    "reckitt": ("Dettol prints the manufacture and expiry dates openly "
                "on the pack — read those. There is no verified public "
                "decoder for Reckitt batch codes."),
}

_UNKNOWN_NOTE = ("No verified decoder for this brand yet. Vouch decodes "
                 "Nivea and the P&G group (Olay, Head & Shoulders, Oral-B, "
                 "Gillette, Ariel, Safeguard), and points Unilever, "
                 "Colgate and Dettol packs to their printed dates.")


def match_group(brand_text: str) -> str:
    """Which maker's rules, if any, cover this brand text."""
    normed = _norm(brand_text)
    if len(normed) <= 2:
        return ""
    for group, aliases in _ALIASES.items():
        for alias in aliases:
            if f" {alias} " in normed:
                return group
    return ""


def _decade_years(digit: int, today: date) -> list[int]:
    base = (today.year // 10) * 10 + digit
    years = []
    year = base
    while year >= 1990:
        years.append(year)
        year -= 10
    return years


def _ambiguity_note(alts: list[int]) -> str:
    if not alts:
        return ""
    shown = ", ".join(str(y) for y in alts[:3])
    return (f"Single-digit years repeat every decade — {shown} fit the "
            "same code. Judge by the pack's condition and any printed "
            "dates.")


def _decode_beiersdorf(code: str, today: date) -> Decode | None:
    m = re.fullmatch(r"(\d)(\d{2})(\d{5})([A-Z]{2})?", code)
    if not m:
        return None
    week = int(m.group(2))
    if not 1 <= week <= 53:
        return None
    made: date | None = None
    alts: list[int] = []
    for year in _decade_years(int(m.group(1)), today):
        try:
            cand = date.fromisocalendar(year, week, 1)
        except ValueError:
            continue  # week 53 does not exist most years
        if cand <= today:
            if made is None:
                made = cand
            else:
                alts.append(year)
    if made is None:
        return None
    return Decode(
        confidence="decoded", group="beiersdorf", brand_label="Nivea",
        made=made, precision="week", alt_years=alts,
        method=("First digit = last digit of the year; next two = "
                "production week."),
        note=_ambiguity_note(alts))


def _decode_pg(code: str, today: date) -> Decode | None:
    m = re.fullmatch(r"(\d)(\d{3})([A-Z0-9]{4,})", code)
    if not m:
        return None
    day = int(m.group(2))
    if not 1 <= day <= 366:
        return None
    made: date | None = None
    alts: list[int] = []
    for year in _decade_years(int(m.group(1)), today):
        cand = date(year, 1, 1) + timedelta(days=day - 1)
        if cand.year != year or cand > today:
            continue  # day 366 in a common year, or the future
        if made is None:
            made = cand
        else:
            alts.append(year)
    if made is None:
        return None
    return Decode(
        confidence="decoded", group="pg", brand_label="P&G",
        made=made, precision="day", alt_years=alts,
        method=("First digit = last digit of the year; next three = "
                "day of the year."),
        note=_ambiguity_note(alts))


_DECODERS = {"beiersdorf": _decode_beiersdorf, "pg": _decode_pg}


def decode(brand_text: str, code: str,
           today: date | None = None) -> Decode:
    """Made-date from a batch code, or an honest non-answer."""
    today = today or date.today()
    group = match_group(brand_text)
    if not group:
        return Decode(confidence="unknown", note=_UNKNOWN_NOTE)
    if group in _GUIDANCE:
        return Decode(confidence="guidance", group=group,
                      brand_label=_LABELS[group], note=_GUIDANCE[group])
    cleaned = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    hit = _DECODERS[group](cleaned, today)
    if hit is not None:
        return hit
    shape = ("8 digits, first = year, next two = week"
             if group == "beiersdorf"
             else "a digit, 3 day-digits, then plant characters")
    return Decode(confidence="unknown", group=group,
                  brand_label=_LABELS[group],
                  note=(f"That code does not match the documented "
                        f"{_LABELS[group]} shape ({shape}). {note_hint()}"))


def note_hint() -> str:
    return ("Check the code was read fully — batch codes hide on crimps, "
            "bases and pump shoulders.")


def supported() -> list[dict]:
    """Brand groups for pickers. Mode is decode or guidance, never both."""
    return [
        {"id": "beiersdorf", "label": "Nivea",
         "brands": ["Nivea"], "mode": "decode"},
        {"id": "pg", "label": "P&G",
         "brands": ["Olay", "Head & Shoulders", "Oral-B", "Gillette",
                    "Ariel", "Safeguard", "Pantene", "Crest"],
         "mode": "decode"},
        {"id": "unilever", "label": "Unilever",
         "brands": ["Dove", "Lux", "Vaseline", "Close-Up", "Rexona",
                    "Axe", "Lifebuoy", "Pepsodent"],
         "mode": "guidance"},
        {"id": "colgate", "label": "Colgate",
         "brands": ["Colgate", "Palmolive"], "mode": "guidance"},
        {"id": "reckitt", "label": "Dettol",
         "brands": ["Dettol"], "mode": "guidance"},
    ]
