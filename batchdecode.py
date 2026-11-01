"""
Vouch — batch-code manufacture-date decoding (pilot).

Three verified algorithms, each corroborated by at least two independent public
sources (retailer Q&As quoting the makers, plus decoder references):

- Beiersdorf (Nivea): 8 digits, first = last digit of the year, next
  two = production week. Trailing plant letters allowed.
- P&G (Olay, Head & Shoulders, Oral-B, Gillette, Ariel, Safeguard):
  first digit = last digit of the year, next three = day of the year.
- L'Oreal group (Garnier, Maybelline, L'Oreal Paris, CeraVe, Dark &
  Lovely and kin): two plant characters, a year letter, a month
  character (1-9, O, N, D), then the lot. Older 5-character codes use
  origin letter + year letter + 3 day-digits instead.

Brands whose makers print open dates (Unilever, Colgate, Reckitt) get
guidance to those dates. Everything else answers "not verified" —
a guessed decode would be an invented fact, and this module will not
produce one.

Year marks repeat on a cycle (every decade for digits, about every
22 years for letters), so every decode carries all plausible years,
most recent first. Callers must show the ambiguity, never just the
top date.
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
    "loreal": ["garnier", "maybelline", "loreal", "l oreal", "cerave",
               "dark lovely", "dark and lovely", "nyx", "lancome",
               "kiehls", "kiehl s", "yves saint laurent", "ysl"],
    "unilever": ["dove", "lux", "vaseline", "close up", "closeup",
                 "rexona", "axe", "lifebuoy", "pepsodent"],
    "colgate": ["colgate", "palmolive"],
    "reckitt": ["dettol"],
}

_LABELS = {
    "beiersdorf": "Nivea",
    "pg": "P&G",
    "loreal": "L'Oreal",
    "unilever": "Unilever",
    "colgate": "Colgate",
    "reckitt": "Reckitt",
}

# L'Oreal year letters: A=2004, running forward, skipping I, O, Q and V
# (lookalikes and reserved). Anchored by dated reports at both ends —
# A=2004..E=2008 on the old 5-character codes, M=2015..U=2021 and Y=2024
# on the current format — so M-Z is firmer ground than A-L.
_LOREAL_YEARS = {
    "A": 2004, "B": 2005, "C": 2006, "D": 2007, "E": 2008, "F": 2009,
    "G": 2010, "H": 2011, "J": 2012, "K": 2013, "L": 2014, "M": 2015,
    "N": 2016, "P": 2017, "R": 2018, "S": 2019, "T": 2020, "U": 2021,
    "W": 2022, "X": 2023, "Y": 2024, "Z": 2025,
}
_LOREAL_CYCLE = 22  # usable letters before the sequence repeats
_LOREAL_MONTHS = {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6,
                  "7": 7, "8": 8, "9": 9, "O": 10, "N": 11, "D": 12}

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
                 "Nivea, the P&G group (Olay, Head & Shoulders, Oral-B, "
                 "Gillette, Ariel, Safeguard) and the L'Oreal group "
                 "(Garnier, Maybelline, L'Oreal Paris, Dark & Lovely), "
                 "and points Unilever, Colgate and Dettol packs to their "
                 "printed dates.")


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


def _ambiguity_note(alts: list[int], cycle: str = "") -> str:
    if not alts:
        return ""
    shown = ", ".join(str(y) for y in alts[:3])
    lead = cycle or "Single-digit years repeat every decade"
    return (f"{lead} — {shown} fit the same code. Judge by the pack's "
            "condition and any printed dates.")


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


def _loreal_years(letter: str, today: date) -> list[int]:
    base = _LOREAL_YEARS[letter]
    years = []
    year = base
    while year >= 1990:
        years.append(year)
        year -= _LOREAL_CYCLE
    year = base + _LOREAL_CYCLE
    while year <= today.year + 1:
        years.append(year)
        year += _LOREAL_CYCLE
    return sorted((y for y in years if y >= 1990), reverse=True)


def _decode_loreal(code: str, today: date) -> Decode | None:
    made: date | None = None
    alts: list[int] = []
    letter = ""
    m = re.fullmatch(r"[A-Z0-9]{2}([A-Z])([1-9OND])([A-Z0-9]+)", code)
    if m and len(code) >= 6:
        letter = m.group(1)
        if letter not in _LOREAL_YEARS:
            return None
        month = _LOREAL_MONTHS[m.group(2)]
        for year in _loreal_years(letter, today):
            cand = date(year, month, 1)
            if cand <= today:
                if made is None:
                    made = cand
                else:
                    alts.append(year)
        method = ("Two plant characters, a year letter, a month "
                  "character (1-9, O, N, D), then the lot.")
        precision = "month"
    else:
        b = re.fullmatch(r"[A-Z]([A-H])(\d{3})", code)
        if not b:
            return None
        letter = b.group(1)
        day = int(b.group(2))
        if not 1 <= day <= 366:
            return None
        cand = date(_LOREAL_YEARS[letter], 1, 1) + timedelta(days=day - 1)
        if cand.year != _LOREAL_YEARS[letter] or cand > today:
            return None
        made = cand
        method = ("Older L'Oreal format: origin letter, year letter, "
                  "3 day-digits.")
        precision = "day"
    if made is None:
        return None
    note = _ambiguity_note(alts, "Year letters repeat about every 22 years")
    if letter and letter < "M":
        note += (" Year letters A-L rest on fewer dated reports than "
                 "M-Z.")
    if made.year > 2025 or any(y > 2025 for y in alts):
        note += (" Years after 2025 assume the letter cycle restarted — "
                 "treat with extra care.")
    return Decode(
        confidence="decoded", group="loreal", brand_label="L'Oreal",
        made=made, precision=precision, alt_years=alts,
        method=method, note=note.strip())


_DECODERS = {"beiersdorf": _decode_beiersdorf, "pg": _decode_pg,
             "loreal": _decode_loreal}


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
    shape = {"beiersdorf": "8 digits, first = year, next two = week",
               "pg": "a digit, 3 day-digits, then plant characters",
               "loreal": ("2 plant characters, a year letter, a month "
                          "character, then the lot")}.get(group, "a known shape")
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
        {"id": "loreal", "label": "L'Oreal",
         "brands": ["Garnier", "Maybelline", "L'Oreal Paris", "CeraVe",
                    "Dark & Lovely", "NYX", "Lancome", "Kiehl's"],
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
