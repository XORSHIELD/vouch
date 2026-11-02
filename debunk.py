"""
Vouch — forwarded-list checker.

Viral WhatsApp lists name "fake" products NAFDAC never warned about,
mixed with a few it did. Paste the list; each line is checked against
the local register mirror and the imported NAFDAC warnings, and the
answer counts how many lines match something real.

Mirror-only: without a synced register this refuses instead of running
thirty slow live lookups. A line that matches nothing is "not matched",
never "genuine" — absence from the mirror is not a finding.
"""

from __future__ import annotations

import re

import registry as R
import safety

MAX_ITEMS = 30
MAX_TEXT = 8000

_BULLET = re.compile(r"^\s*(?:\d+[.)\-:]\s*|[-*•]+\s*)")


def items_of(text: str) -> list[str]:
    """One pasted list to clean lines. Bullets and numbers stripped."""
    out = []
    for line in (text or "")[:MAX_TEXT].splitlines():
        line = _BULLET.sub("", line).strip(" \t-–—:;\"'")
        if line:
            out.append(line[:200])
        if len(out) >= MAX_ITEMS:
            break
    return out


def _name_hits(item: str) -> int:
    try:
        return R.search(item, length=3).get("recordsFiltered", 0) or 0
    except Exception:
        return 0


def _alert_hits(item: str) -> list[dict]:
    try:
        found = safety.alerts(query=item)
    except Exception:
        return []
    if not found.get("configured"):
        return []
    return [{"id": a.get("id", ""), "reason": a.get("reason", "")[:160],
             "published": a.get("published", ""), "url": a.get("url", "")}
            for a in found.get("alerts", [])[:3]]


def check_item(item: str) -> dict:
    """One line against the mirror and the warnings."""
    number = R.extract_number(item)
    verdict, registered_name = "", ""
    if number:
        try:
            res = R.lookup(number, offline=True)
        except R.RegistryUnavailable:
            res = None
        if res is not None and res.verdict in (
                R.REGISTERED, R.MISMATCH, R.INACTIVE):
            verdict = res.verdict
            if res.matched:
                registered_name = res.matched.product_name
    return {"text": item, "nafdac": number, "register": verdict,
            "registered_name": registered_name,
            "name_hits": 0 if number else _name_hits(item),
            "alerts": _alert_hits(item)}


def check(text: str) -> dict:
    """A whole pasted list, counted honestly."""
    items = items_of(text)
    if not items:
        return {"ok": False, "items": [], "counts": {},
                "message": "Paste a list of product names or NAFDAC "
                           "numbers, one per line."}
    if not R.mirror_ready():
        return {"ok": False, "items": [], "counts": {},
                "message": "The register is not synced on this machine, "
                           "so lines cannot be checked. Sync it first."}
    out = [check_item(item) for item in items]
    counts = {"checked": len(out),
              "in_register": sum(1 for r in out if r["register"]
                                 or r["name_hits"]),
              "with_alerts": sum(1 for r in out if r["alerts"]),
              "unmatched": sum(1 for r in out if not r["register"]
                               and not r["name_hits"] and not r["alerts"])}
    return {"ok": True, "items": out, "counts": counts,
            "note": ("'Unmatched' means no register or warning entry was "
                     "found — not that the product is genuine, and not "
                     "that a warning does not exist elsewhere.")}
