"""
Vouch — planner playbooks.

Small markdown workflows, one file per task, loaded by topic. Like
skills in a coding agent: the planner reads only the playbook that
matches the message instead of carrying every procedure at once.

File format (playbooks/*.md):

    # Title

    Triggers: word, multi word, ...

    Body lines...

The deterministic pipeline never sees these; they brief the model
planner only, and the engine still owns every verdict.
"""

from __future__ import annotations

import glob
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "playbooks")
MAX_BRIEF = 1500

_TRIGGERS = re.compile(r"^Triggers:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_TITLE = re.compile(r"^#\s+(.+)$", re.MULTILINE)

_HONESTY = ("Honesty rule: never declare a pack real, fake, genuine, "
            "safe or counterfeit — quote the engine's band.")

_cache: list[tuple[str, str, list[str], str]] | None = None


def _load() -> list[tuple[str, str, list[str], str]]:
    """(name, title, triggers, body) per playbook file. Empty, never loud."""
    global _cache
    if _cache is not None:
        return _cache
    out: list[tuple[str, str, list[str], str]] = []
    try:
        paths = sorted(glob.glob(os.path.join(DIR, "*.md")))
    except OSError:
        paths = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        name = os.path.splitext(os.path.basename(path))[0]
        found = _TITLE.search(text)
        title = found.group(1).strip() if found else name
        trig = _TRIGGERS.search(text)
        triggers = [t.strip().lower() for t in trig.group(1).split(",")
                    if t.strip()] if trig else []
        body = _TRIGGERS.sub("", text)
        body = _TITLE.sub("", body).strip()
        if triggers and body:
            out.append((name, title, triggers, body))
    _cache = out
    return out


def match(message: str) -> list[tuple[str, str]]:
    """(name, body) of playbooks whose triggers hit, best first."""
    msg = (message or "").lower()
    if not msg.strip():
        return []
    scored = []
    for name, _title, triggers, body in _load():
        hits = sum(1 for t in triggers if t in msg)
        if hits:
            scored.append((-hits, name, body))
    scored.sort()
    return [(name, body) for _, name, body in scored]


def briefing(message: str) -> str:
    """Matched playbooks as one capped briefing. Empty when none match."""
    bits = []
    for _name, body in match(message):
        bits.append(body)
        if sum(len(b) for b in bits) >= MAX_BRIEF:
            break
    if not bits:
        return ""
    text = "\n\n".join(bits)[:MAX_BRIEF].rstrip()
    return f"{text}\n\n{_HONESTY}"
