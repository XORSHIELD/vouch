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

print("no loops")
import re as _re
from engine import Action, Assessment


def _next_sent(text: str) -> str:
    m = _re.search(r"Next: .*?(?:\.|$)", text)
    return m.group(0) if m else ""


replies = convo("6034000005424", "BB 290927AJ57", "300926C 10:16")
n2, n3 = _next_sent(replies[1]["answer"]), _next_sent(replies[2]["answer"])
check("next step not parroted across turns",
      n2 != n3 or not n3, f"{n2!r} vs {n3!r}")
made = Assessment(band="incomplete", headline="Not enough yet",
                  checks=[], score=0.0, registry=None, input={},
                  actions=[Action("a", "Do the first thing", "why-1", 0),
                           Action("b", "Do the second thing", "why-2", 0)])
rotated = agent._next_line(
    made, [{"role": "user", "text": "hi"},
           {"role": "assistant",
            "text": "Hello. Next: Do the first thing — why-1"}])
check("suggestions rotate instead of repeating",
      "second thing" in rotated, rotated)
repeat_reply = convo("6034000005424", "6034000005424")[1]["answer"]
check("repeated detail noticed, not re-acked",
      "already" in repeat_reply.lower(), repeat_reply[:160])

print("batch made-date")
made = convo("this is Nivea Soft", "batch 64010123",
             "when was it made?")
check("batch fragment decoded in passing",
      "Sep 2026" in made[1]["answer"], made[1]["answer"][:200])
check("made-date answered with ambiguity",
      "Sep 2026" in made[2]["answer"]
      and "2016" in made[2]["answer"], made[2]["answer"][:300])
guided = convo("this is Dove soap", "batch 12661", "how old is it?")
check("open-dated brand guided",
      "openly" in guided[2]["answer"], guided[2]["answer"][:200])
unknown = convo("this is Avon", "batch X1Y2", "how old is it?")
check("unverified brand honest",
      "No verified decoder" in unknown[2]["answer"],
      unknown[2]["answer"][:200])
dudu = convo("this is Dudu Osun", "batch ABC123", "how old is it?")
check("dudu osun guided to the box",
      "end of the box" in dudu[2]["answer"], dudu[2]["answer"][:200])

print("pao")
pao = convo("the jar says 12M", "I opened it in June")
check("pao months remembered, opening asked",
      "12M" in pao[0]["answer"] and "open" in pao[0]["answer"].lower(),
      pao[0]["answer"][:160])
check("pao answer dated",
      "Jun 2027" in pao[1]["answer"], pao[1]["answer"][:200])
over = agent.chat("my cream is 6M, opened in March")
check("passed pao honest",
      "Sep 2026" in over["answer"] and "passed" in over["answer"],
      over["answer"][:200])
hijack = agent.chat("I ran 5M in June, feeling great")
check("non-cosmetic 5M not hijacked",
      "runs out" not in hijack["answer"], hijack["answer"][:200])

print("single-turn checks unchanged")
r = agent.chat("NAFDAC A99-9999, open a case")
check("explicit check still proposes",
      (r.get("proposal") or {}).get("action") == "open_case"
      and r.get("provider") == "vouch-composer",
      str(r.get("proposal"))[:160])

print("photo acknowledgment")
import vision as _vision
_real_extract = _vision.extract_from_image
try:
    _vision.extract_from_image = lambda *a, **k: _vision.Extraction()
    r = agent.chat("", [], b"fake-photo-bytes", "image/jpeg")
    check("empty photo read is acknowledged",
          "received your photo" in r["answer"].lower()
          and "could not read" in r["answer"].lower(),
          r["answer"][:200])
    check("empty photo keeps its extraction + assessment",
          r.get("extraction") is not None
          and r.get("assessment") is not None)
    _vision.extract_from_image = lambda *a, **k: _vision.Extraction(
        nafdac_number="A11-0551", product_name="AC-Drex Tablet")
    r2 = agent.chat("", [], b"fake-photo-bytes", "image/jpeg")
    check("readable photo needs no apology",
          "could not read" not in r2["answer"].lower(),
          r2["answer"][:200])
    check("readable photo drives the assessment",
          r2["assessment"].get("input", {}).get("nafdac_number")
          == "A11-0551",
          repr(r2["assessment"].get("input"))[:160])
finally:
    _vision.extract_from_image = _real_extract

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
