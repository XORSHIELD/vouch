"""
Vouch — NAPAMS reply reader.

Food, water and cosmetics live in NAFDAC's NAPAMS portal, which sits
behind a CAPTCHA: vouch points people there but never scrapes it.
Instead the person pastes the portal's reply here, and this module
reads it — the same quoted-statement pattern as MAS replies.

Verdicts are deliberately narrow: "registered" needs explicit positive
words about the expected number, "not_found" needs explicit negative
words, and anything mixed, numberless-but-unclear, or about a different
number comes back unclear or wrong_number with the quote attached.
"""

from __future__ import annotations

import re

REGISTERED = "registered"
NOT_FOUND = "not_found"
UNCLEAR = "unclear"
WRONG_NUMBER = "wrong_number"

_POSITIVE = ("registered", "approved", "valid registration",
             "genuine", "authentic")
_NEGATIVE = ("not found", "not registered", "invalid", "no record",
             "does not exist", "do not exist", "unregistered",
             "not authentic")
_NUMBER = re.compile(r"\b[A-Z]{0,2}\d{1,2}-\d{4,6}\b")
_MAX_QUOTE = 220


def _canon(number: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (number or "").upper())


def _about(text: str) -> str:
    m = _NUMBER.search((text or "").upper())
    return m.group(0) if m else ""


def read_reply(text: str, number: str = "") -> dict:
    """Read a pasted NAPAMS reply. Never raises, never guesses."""
    try:
        return _read((text or "").strip(), _canon(number))
    except Exception:
        return {"verdict": UNCLEAR, "about": "", "expected": number,
                "quote": (text or "").strip()[:_MAX_QUOTE]}


def _read(text: str, expected: str) -> dict:
    about = _about(text)
    quote = text[:_MAX_QUOTE]
    out = {"verdict": UNCLEAR, "about": about, "expected": expected,
           "quote": quote}
    if not text:
        return out
    if expected and about and _canon(about) != expected:
        out["verdict"] = WRONG_NUMBER
        return out
    low = text.lower()
    neg = any(w in low for w in _NEGATIVE)
    scrubbed = low
    for phrase in _NEGATIVE:
        scrubbed = scrubbed.replace(phrase, " ")
    pos = any(w in scrubbed for w in _POSITIVE)
    if pos and not neg:
        out["verdict"] = REGISTERED
    elif neg and not pos:
        out["verdict"] = NOT_FOUND
    return out
