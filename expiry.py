"""
Vouch — expiry and manufacture-date parsing.

Packs in Nigerian markets print dates in at least a dozen shapes, often with
two-digit years, month names, or no separator at all. A parser that guesses
wrong produces the worst possible outcome: calling a valid pack expired.

Principle: when a date is ambiguous, say so. Never resolve ambiguity silently
into an accusation.

Conservative bias: for an expiry date we use the LATEST plausible instant
(end of the stated month, or 31 December for a bare year). That way we only
call something expired when it is expired under every reading.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Words that introduce a date, used to find the value next to them.
EXP_LABELS = r"(?:EXP(?:IRY|IRES|IRATION)?|EXP\.?\s*DATE|USE\s*BY|BEST\s*BEFORE|BBE)"
MFG_LABELS = r"(?:MFG|MFD|MAN(?:UFACTURED)?|PROD(?:UCTION)?|P\.?\s*DATE)"


@dataclass
class ParsedDate:
    year: int
    month: int | None
    day: int | None
    raw: str
    precision: str          # "day" | "month" | "year"
    ambiguous: bool = False
    note: str = ""

    def end_of_period(self) -> date:
        """Latest instant this date can mean. Conservative for expiry checks."""
        if self.precision == "year":
            return date(self.year, 12, 31)
        if self.month is None:
            return date(self.year, 12, 31)
        last = calendar.monthrange(self.year, self.month)[1]
        if self.precision == "day" and self.day:
            return date(self.year, self.month, min(self.day, last))
        return date(self.year, self.month, last)

    def start_of_period(self) -> date:
        if self.precision == "year" or self.month is None:
            return date(self.year, 1, 1)
        return date(self.year, self.month, self.day or 1)


def _fix_year(y: int) -> tuple[int, bool]:
    """Expand a two-digit year. Returns (year, guessed)."""
    if y >= 100:
        return y, False
    # NAFDAC packs are overwhelmingly 2020-2035. Map 00-69 -> 2000s.
    return (2000 + y) if y < 70 else (1900 + y), True


def parse_date(text: str | None, today: date | None = None) -> ParsedDate | None:
    """Parse the first date-like token in `text`."""
    if not text:
        return None
    s = str(text).upper().strip()
    if not s:
        return None
    s = re.sub(r"[.,]", " ", s)
    s = re.sub(r"\s+", " ", s)

    # Order matters: the most specific shapes first, bare year last.

    # 1. Day + month name + year: "15 MAR 2027"
    m = re.search(r"\b(\d{1,2})\s+([A-Z]{3,4})\s+(\d{2,4})\b", s)
    if m and m.group(2)[:3] in MONTHS:
        mo = MONTHS[m.group(2)[:3]]
        y, guessed = _fix_year(int(m.group(3)))
        return ParsedDate(y, mo, int(m.group(1)), m.group(0), "day",
                          ambiguous=guessed)

    # 2. Month name + year: "MAR 2027", "MAR-27"
    m = re.search(r"\b([A-Z]{3,4})\s*[-/ ]?\s*(\d{2,4})\b", s)
    if m and m.group(1)[:3] in MONTHS:
        mo = MONTHS[m.group(1)[:3]]
        y, guessed = _fix_year(int(m.group(2)))
        return ParsedDate(y, mo, None, m.group(0), "month", ambiguous=guessed)

    # 3. ISO / year-first: "2027-03-15", "2027/03"
    m = re.search(r"\b(20\d{2}|19\d{2})\s*[-/ ]\s*(\d{1,2})(?:\s*[-/ ]\s*(\d{1,2}))?\b", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), m.group(3)
        if 1 <= mo <= 12:
            day = int(d) if d and 1 <= int(d) <= 31 else None
            return ParsedDate(y, mo, day, m.group(0), "day" if day else "month")

    # 4. Day-or-month / month / year: "15/03/2027", "03/15/2027"
    m = re.search(r"\b(\d{1,2})\s*[-/ ]\s*(\d{1,2})\s*[-/ ]\s*(20\d{2}|19\d{2})\b", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= a <= 31 and 1 <= b <= 12:
            ambiguous = a <= 12           # could be read either way
            return ParsedDate(y, b, a, m.group(0), "day",
                              ambiguous=ambiguous,
                              note="day/month order assumed (Nigerian convention)"
                              if ambiguous else "")
        if 1 <= b <= 31 and 1 <= a <= 12:
            return ParsedDate(y, a, b, m.group(0), "day", ambiguous=True,
                              note="month/day order")

    # 5. Month / year: "03/2027"
    m = re.search(r"\b(\d{1,2})\s*[-/ ]\s*(20\d{2}|19\d{2})\b", s)
    if m:
        mo, y = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return ParsedDate(y, mo, None, m.group(0), "month")

    # 6. Month / two-digit year: "03/27"
    m = re.search(r"\b(\d{1,2})\s*[-/ ]\s*(\d{2})\b", s)
    if m:
        mo, yy = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            y, guessed = _fix_year(yy)
            return ParsedDate(y, mo, None, m.group(0), "month",
                              ambiguous=guessed,
                              note="two-digit year" if guessed else "")

    # 7. Bare 4-digit year, last resort.
    m = re.search(r"\b(20\d{2}|19\d{2})\b", s)
    if m:
        return ParsedDate(int(m.group(1)), None, None, m.group(0), "year")

    return None


def parse_labelled(text: str, labels: str) -> ParsedDate | None:
    """Find the date that follows a label such as EXP or MFG."""
    if not text:
        return None
    s = str(text).upper()
    m = re.search(labels + r"\s*[:.\-]?\s*([0-9A-Z][0-9A-Z\s/\-\.]{2,18})", s)
    if m:
        got = parse_date(m.group(1))
        if got:
            return got
    return parse_date(s)


# --------------------------------------------------------------------------
# Assessment
# --------------------------------------------------------------------------

VALID = "valid"
EXPIRED = "expired"
EXPIRING = "expiring"          # within the attention window
IMPLAUSIBLE = "implausible"
SEQUENCE_ERROR = "sequence_error"
UNREADABLE = "unreadable"


@dataclass
class ExpiryAssessment:
    state: str
    message: str
    exp: ParsedDate | None = None
    mfg: ParsedDate | None = None
    days_left: int | None = None
    signals: list[str] = None

    def __post_init__(self) -> None:
        if self.signals is None:
            self.signals = []


def assess_expiry(
    printed_expiry: str | None,
    printed_mfg: str | None = None,
    today: date | None = None,
    attention_days: int = 90,
) -> ExpiryAssessment:
    today = today or date.today()
    exp = parse_labelled(printed_expiry, EXP_LABELS) or parse_date(printed_expiry)
    mfg = parse_labelled(printed_mfg, MFG_LABELS) or parse_date(printed_mfg)

    signals: list[str] = []

    if exp is None:
        return ExpiryAssessment(
            UNREADABLE,
            "No readable expiry date on the pack.",
            mfg=mfg,
            signals=["An unreadable or absent expiry is itself a packaging defect."],
        )

    if exp.year < 2015 or exp.year > 2045:
        return ExpiryAssessment(
            IMPLAUSIBLE,
            f"Expiry year {exp.year} is outside any plausible shelf life.",
            exp=exp, mfg=mfg,
            signals=["Check for a misprint or a re-stamped date."],
        )

    if exp.ambiguous:
        signals.append(
            "The year was printed with two digits, so it was expanded automatically."
        )

    if mfg is not None:
        if mfg.start_of_period() > exp.end_of_period():
            return ExpiryAssessment(
                SEQUENCE_ERROR,
                "Manufacture date is later than the expiry date.",
                exp=exp, mfg=mfg,
                signals=["Impossible sequence: the pack cannot be both "
                         "made after and expiring before."],
            )
        months = (exp.year - mfg.year) * 12 + ((exp.month or 1) - (mfg.month or 1))
        if months > 72:
            signals.append(
                f"Stated shelf life is {months} months, unusually long for a medicine."
            )
        elif months < 0:
            signals.append("Dates are internally inconsistent.")

    days_left = (exp.end_of_period() - today).days

    if days_left < 0:
        return ExpiryAssessment(
            EXPIRED,
            f"Expired on {exp.end_of_period().isoformat()} "
            f"({abs(days_left)} days ago).",
            exp=exp, mfg=mfg, days_left=days_left, signals=signals,
        )
    if days_left <= attention_days:
        return ExpiryAssessment(
            EXPIRING,
            f"Expires {exp.end_of_period().isoformat()} — {days_left} days left.",
            exp=exp, mfg=mfg, days_left=days_left, signals=signals,
        )

    return ExpiryAssessment(
        VALID,
        f"Expires {exp.end_of_period().isoformat()} — {days_left} days left.",
        exp=exp, mfg=mfg, days_left=days_left, signals=signals,
    )
