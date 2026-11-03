"""
Vouch — NAPAMS reply reader.

Food, water and cosmetics live in NAFDAC's NAPAMS portal, which sits
behind a CAPTCHA: vouch points people there but never scrapes it.
Instead the person pastes the portal's reply here, and this module
reads it — the same quoted-statement pattern as MAS replies.

Two shapes are understood. A PRODUCT DETAILS block (labeled fields
plus the NAFDAC number) is itself proof the portal holds the record,
with an expiry banner meaning the certificate lapsed. Anything else
keys on verdict words: "registered" needs explicit positive words
about the expected number, "not_found" explicit negative words, and
anything mixed, unclear, or about a different number comes back
unclear or wrong_number with the quote attached.
"""

from __future__ import annotations

import json
import os
import re
import time

REGISTERED = "registered"
NOT_FOUND = "not_found"
UNCLEAR = "unclear"
WRONG_NUMBER = "wrong_number"
EXPIRED = "expired"

_POSITIVE = ("registered", "approved", "valid registration",
             "genuine", "authentic")
_NEGATIVE = ("not found", "not registered", "invalid", "no record",
             "does not exist", "do not exist", "unregistered",
             "not authentic")
_EXPIRED_WORDS = ("has expired", "certificate expired",
                  "registration expired", "licence expired",
                  "license expired")
_NUMBER = re.compile(r"\b[A-Z]{0,2}\d{1,2}-\d{4,6}\b")
_MAX_QUOTE = 220

_DETAIL_LABELS = (
    ("product", re.compile(r"product name\s*:\s*(.+)", re.IGNORECASE)),
    ("category", re.compile(r"product category\s*:\s*(.+)",
                            re.IGNORECASE)),
    ("source", re.compile(r"product source\s*:\s*(.+)", re.IGNORECASE)),
    ("manufacturer", re.compile(r"manufacturer\s*:\s*(.+)",
                                re.IGNORECASE)),
    ("approved", re.compile(r"date approved\s*:\s*(.+)", re.IGNORECASE)),
    ("expiry", re.compile(r"expiry date\s*:\s*(.+)", re.IGNORECASE)),
    ("ingredient", re.compile(r"active ingredient\s*:\s*(.+)",
                              re.IGNORECASE)),
)


def _canon(number: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (number or "").upper())


def _about(text: str) -> str:
    m = _NUMBER.search((text or "").upper())
    return m.group(0) if m else ""


def _details(text: str) -> dict:
    """Labeled fields from a PRODUCT DETAILS paste, if present."""
    found: dict[str, str] = {}
    for key, pat in _DETAIL_LABELS:
        m = pat.search(text or "")
        if not m:
            continue
        val = re.sub(r"\s+", " ", m.group(1)).strip()[:120]
        if key == "product":
            val = re.split(r"\s*\(", val)[0].strip() or val
        if val:
            found[key] = val
    return found


def read_reply(text: str, number: str = "") -> dict:
    """Read a pasted NAPAMS reply. Never raises, never guesses."""
    try:
        return _read((text or "").strip(), _canon(number))
    except Exception:
        return {"verdict": UNCLEAR, "about": "", "expected": number,
                "quote": (text or "").strip()[:_MAX_QUOTE],
                "details": {}, "matches": False}


def _read(text: str, expected: str) -> dict:
    about = _about(text)
    quote = text[:_MAX_QUOTE]
    out = {"verdict": UNCLEAR, "about": about, "expected": expected,
           "quote": quote, "details": {}, "matches": False}
    if not text:
        return out
    if expected and about and _canon(about) != expected:
        out["verdict"] = WRONG_NUMBER
        return out
    out["matches"] = bool(expected and about)
    low = text.lower()
    details = _details(text)
    if len(details) >= 2 and ("product details" in low or about):
        out["details"] = details
        if any(w in low for w in _EXPIRED_WORDS):
            out["verdict"] = EXPIRED
        else:
            out["verdict"] = REGISTERED
        return out
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


CACHE_DIR = (os.environ.get("VOUCH_CACHE_DIR")
             or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             ".cache"))
CACHE_FILE = os.path.join(CACHE_DIR, "napams_known.json")
SEED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "napams_seed.json")
MAX_ENTRIES = 2000


def _load() -> dict:
    try:
        with open(CACHE_FILE, encoding="utf-8") as fh:
            body = json.load(fh)
        return body if isinstance(body, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(entries: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=False)


def file_details(res: dict) -> dict | None:
    """File a parsed paste for future lookups. Never raises.

    Only product-details pastes qualify — bare verdict words are too
    easy to fake. Conflicting pastes for one number replace; matching
    ones strengthen the hit count.
    """
    try:
        det = (res or {}).get("details") or {}
        about = (res or {}).get("about") or ""
        if not about or not det.get("product"):
            return None
        if (res or {}).get("matches") is False:
            return None  # paste is about a different number: do not file
        key = _canon(about)
        entries = _load()
        now = time.strftime("%Y-%m-%d")
        prev = entries.get(key) or {}
        if prev.get("product") == det.get("product"):
            hits = int(prev.get("hits", 0)) + 1
            first = prev.get("first_seen", now)
        else:
            hits, first = 1, now
        entry = {"number": about, "product": det.get("product", ""),
                 "maker": det.get("manufacturer", ""),
                 "category": det.get("category", ""),
                 "approved": det.get("approved", ""),
                 "expiry": det.get("expiry", ""),
                 "verdict": (res or {}).get("verdict", ""),
                 "first_seen": first, "hits": hits}
        entries[key] = entry
        if len(entries) > MAX_ENTRIES:
            oldest = sorted(entries,
                            key=lambda k: entries[k].get("first_seen", ""))
            for gone in oldest[:len(entries) - MAX_ENTRIES]:
                del entries[gone]
        _save(entries)
        return entry
    except Exception:
        return None


def _load_seed() -> dict:
    """Committed listings, transcribed from portal replies. Never raises."""
    try:
        with open(SEED_FILE, encoding="utf-8") as fh:
            body = json.load(fh)
        return body if isinstance(body, dict) else {}
    except (OSError, ValueError):
        return {}


def known(number: str) -> dict | None:
    """A previously filed NAPAMS entry, or None. Never raises.

    Fresh pastes win; the shipped seed answers numbers nobody on
    this machine has pasted yet.
    """
    try:
        key = _canon(number)
        if not key:
            return None
        hit = _load().get(key)
        if hit is not None:
            return hit
        return _load_seed().get(key)
    except Exception:
        return None
