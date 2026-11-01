"""
Agent conversation tests: the agent must listen across turns.

Regression: every chat message was assessed in isolation, so batch codes
were misread as NAFDAC numbers, corrections were ignored, and each reply
was the same canned verdict dump. These tests replay that transcript.

Run:  python test_agent.py
"""

from __future__ import annotations

import os

os.environ.pop("MODEL_API_KEY", None)
os.environ.pop("META_API_KEY", None)

import agent

agent.API_KEY = ""

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


def convo(*user_turns: str) -> list[dict]:
    """Run turns through agent.chat with UI-style history. Returns replies."""
    history: list[dict] = []
    replies = []
    for turn in user_turns:
        r = agent.chat(turn, history)
        replies.append(r)
        history.append({"role": "user", "text": turn})
        history.append({"role": "assistant",
                        "text": r.get("answer", "")})
    return replies


print("field parsing")
f = agent._extract_fields("BB 290927AJ57")
check("batch code is batch, not NAFDAC",
      f.get("batch") == "BB 290927AJ57" and not f.get("nafdac_number"),
      repr(f))
f = agent._extract_fields("300926C 10:16")
check("date code is a pack code, not NAFDAC",
      f.get("batch") and not f.get("nafdac_number"), repr(f))
f = agent._extract_fields("here is the badrcode 6034000005424")
check("barcode digits found despite typo",
      f.get("barcode") == "6034000005424", repr(f))
f = agent._extract_fields("NAFDAC A1-2345")
check("dashed number is NAFDAC", f.get("nafdac_number") == "A1-2345",
      repr(f))
f = agent._extract_fields("my nafdac number is B2 3456")
check("labelled number is NAFDAC without dash",
      f.get("nafdac_number") == "B2 3456", repr(f))
f = agent._extract_fields("call 08031234567")
check("phone number is not NAFDAC", not f.get("nafdac_number"),
      repr(f))
f = agent._extract_fields("this is a water not a medicine AQUAFINA")
check("correction heard",
      f.get("kind_word") == "water" and "aquafina" in
      (f.get("product_name") or "").lower(), repr(f))

print("transcript replay")
replies = convo(
    "Hello, can you hear me?",
    "OK, so can you help me scan the barcode?",
    "here is the badrcode 6034000005424",
    "BB 290927AJ57",
    "300926C 10:16",
    "this is a water not a medicine AQUAFINA",
)
mic, howto, barcode, batch, datecode, correction = (
    r.get("answer", "") for r in replies)
check("mic check answered",
      "hear you" in mic.lower() and "not enough to go on" not in mic,
      mic[:160])
check("scan help guides instead of verdict-dump",
      "not enough to go on" not in howto
      and ("scan" in howto.lower() or "camera" in howto.lower()),
      howto[:200])
check("barcode acknowledged, no canned dump",
      "6034000005424" in barcode
      and "You can ask me why" not in barcode, barcode[:200])
check("batch kept as batch",
      "batch" in batch.lower() and "no registry record" not in batch.lower(),
      batch[:200])
check("date code not treated as NAFDAC",
      "no registry record" not in datecode.lower(), datecode[:200])
check("correction accepted in words",
      "water" in correction.lower()
      and "aquafina" in correction.lower(), correction[:200])
check("correction accepted in assessment",
      replies[5]["assessment"].get("kind") == "water",
      replies[5]["assessment"].get("kind"))
check("barcode remembered across turns",
      replies[5]["assessment"].get("input", {}).get("barcode")
      == "6034000005424",
      repr(replies[5]["assessment"].get("input"))[:200])

print("single-turn checks unchanged")
r = agent.chat("NAFDAC A99-9999, open a case")
check("explicit check still proposes",
      (r.get("proposal") or {}).get("action") == "open_case"
      and r.get("provider") == "vouch-composer",
      str(r.get("proposal"))[:160])

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
