"""
Vouch — audit log.

Who did what, when. Append-only JSON lines, one event per line, so a
regulator or an admin can reconstruct what happened to a case or a report.
Never stores passwords, tokens, or audio.
"""

from __future__ import annotations

import json
import os
import time

LOG_PATH = (os.environ.get("VOUCH_AUDIT_LOG")
            or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            ".data", "audit.log"))


def log(event: str, actor: str = "", detail: str = "") -> None:
    """Append one event. Logging must never break the request it records."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                "event": str(event or "")[:64],
                "actor": str(actor or "")[:64],
                "detail": str(detail or "")[:300],
            }) + "\n")
    except Exception:
        pass


def recent(limit: int = 50) -> list[dict]:
    """Last N events, newest first. Empty when nothing was ever logged."""
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return []
    out = []
    for line in lines[-max(1, limit):]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out[::-1]


def summary(limit: int = 2000) -> dict:
    """Event counts over the recent log. Empty when nothing was logged."""
    counts: dict[str, int] = {}
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return {"events": {}, "scanned": 0}
    scanned = 0
    for line in lines[-max(1, limit):]:
        try:
            ev = json.loads(line).get("event", "")
        except Exception:
            continue
        if ev:
            counts[ev] = counts.get(ev, 0) + 1
            scanned += 1
    return {"events": counts, "scanned": scanned}
