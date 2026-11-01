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
os.environ.pop("VOUCH_MAS_URL", None)

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

print("scratch PIN")
f = agent._extract_fields("my pin is 2062775840753")
check("typed PIN parsed",
      f.get("pin") == "2062775840753"
      and not f.get("barcode") and not f.get("nafdac_number"), repr(f))
f = agent._extract_fields("Text 2062775840753 to 34488")
check("SMS instruction gives PIN + shortcode",
      f.get("pin") == "2062775840753"
      and f.get("shortcode") == "34488"
      and not f.get("barcode"), repr(f))
f = agent._extract_fields("my pin is 6034000005424")
check("PIN beats the EAN-13 shape",
      f.get("pin") == "6034000005424" and not f.get("barcode"),
      repr(f))
f = agent._extract_fields("dial *34488*1*2062775840753# to verify")
check("USSD dial string parsed",
      f.get("pin") == "2062775840753"
      and f.get("shortcode") == "34488", repr(f))
f = agent._extract_fields("shortcode 34488")
check("shortcode alone parsed",
      f.get("shortcode") == "34488" and not f.get("pin"), repr(f))
f = agent._extract_fields("here is the badrcode 6034000005424")
check("barcode typo still a barcode, not a PIN",
      not f.get("pin") and f.get("barcode") == "6034000005424",
      repr(f))
f = agent._extract_fields("batch code ACD1902")
check("batch code is not a PIN", not f.get("pin"), repr(f))
f = agent._extract_fields("call 08031234567")
check("phone number is not a PIN", not f.get("pin"), repr(f))
f = agent._extract_fields("the code is 48291047")
check("bare code word is not enough", not f.get("pin"), repr(f))

ask = convo("i want you to confirm the scratch card number if its genuine")
check("scratch question asks for the PIN",
      "scratch it off" in ask[0]["answer"]
      and "No. The checks" not in ask[0]["answer"],
      ask[0]["answer"][:200])
typed = convo("my pin is 2062775840753, text it to 34488")[0]["answer"]
check("typed PIN gets SMS guidance",
      "Text 2062775840753 to 34488" in typed, typed[:300])
scfirst = convo("shortcode 34488")[0]["answer"]
check("shortcode alone asks for the PIN",
      "Now type the scratch PIN" in scfirst, scfirst[:200])
reuse = convo("my pin is 2062775840753", "did the service reply yet")
check("PIN guidance not parroted",
      "Text 2062775840753" not in reuse[1]["answer"],
      reuse[1]["answer"][:200])

_real_extract2, _mas_real = (_vision.extract_from_image,
                            agent.mas.verify)
try:
    _vision.extract_from_image = lambda *a, **k: _vision.Extraction(
        product_name="TadalisPlus", pin="2062775840753",
        shortcode="34488")
    r = agent.chat("", [], b"fake-photo-bytes", "image/jpeg")
    check("photo PIN gets SMS guidance, no unreadable note",
          "Text 2062775840753 to 34488" in r["answer"]
          and "could not read" not in r["answer"].lower(),
          r["answer"][:300])
    agent.mas.verify = lambda pin, sc="": {
        "connected": True, "verdict": "suspicious",
        "detail": "PIN already used 40 times.", "provider": "pytest",
        "pin": pin, "shortcode": sc}
    r = agent.chat("my pin is 2062775840753", [], None, "image/jpeg")
    check("suspicious PIN quoted with provider",
          "pytest" in r["answer"] and "already used" in r["answer"]
          and "not this app" in r["answer"], r["answer"][:300])
    agent.mas.verify = lambda pin, sc="": {
        "connected": True, "verdict": "authentic",
        "detail": "PIN valid.", "provider": "pytest",
        "pin": pin, "shortcode": sc}
    r = agent.chat("my pin is 2062775840753", [], None, "image/jpeg")
    check("authentic PIN qualified, never Vouch's claim",
          "speaks for the code" in r["answer"], r["answer"][:300])
finally:
    _vision.extract_from_image = _real_extract2
    agent.mas.verify = _mas_real
os.environ["VOUCH_MAS_URL"] = "demo"
try:
    r = agent.chat("my pin is 2062775840753", [], None, "image/jpeg")
    check("demo provider quoted honestly",
          "Demo connector" in r["answer"], r["answer"][:300])
finally:
    os.environ.pop("VOUCH_MAS_URL", None)

print("service replies")
back = convo("my pin is 2062775840753, text it to 34488",
             "Sproxil says PIN VALID")[1]["answer"]
check("pass reply read",
      "reads as a pass" in back
      and "Text 2062775840753" not in back, back[:300])
bare = convo("my pin is 2062775840753", "PIN VALID")[1]["answer"]
check("bare verdict read",
      "reads as a pass" in bare, bare[:200])
fail = convo("my pin is 2062775840753",
             "reply: sorry, PIN already used")[1]["answer"]
check("fail reply read",
      "reads as a fail" in fail
      and "Text 2062775840753" not in fail
      and "every digit" in fail, fail[:400])
vague = convo("my pin is 2062775840753",
              "it says error, try later")[1]["answer"]
check("unclear reply asks for the full text",
      "can't tell" in vague, vague[:200])
mine = convo("my pin is 2062775840753",
             "i used the pin yesterday")[1]["answer"]
check("own narration is not a fail",
      "reads as a fail" not in mine
      and "reads as a pass" not in mine, mine[:200])
claim = convo("pin 12345678 valid")[0]["answer"]
check("own claim is not the service's answer",
      "reads as a pass" not in claim
      and "verifiable" in claim, claim[:300])
opinion = convo("my pin is 2062775840753",
                "feels genuine to me")[1]["answer"]
check("feeling is not a verdict",
      "reads as a pass" not in opinion, opinion[:200])
nopin = convo("is this genuine?")[0]["answer"]
check("no PIN, no reply reading",
      "I didn't catch a pack detail" in nopin
      and "reads as a" not in nopin, nopin[:200])
known = convo("my pin is 2062775840753, text it to 34488",
              "is my pin genuine?")[1]["answer"]
check("genuine question with PIN stays honest + actionable",
      "No. The checks" in known
      and "Text 2062775840753 to 34488" in known, known[:400])
confirm = convo("my pin is 2062775840753, text it to 34488",
                "i want you to confirm the scratch card number "
                "if its genuine")[1]["answer"]
check("confirm-request is not a pass",
      "No. The checks" in confirm
      and "Text 2062775840753 to 34488" in confirm
      and "reads as a pass" not in confirm, confirm[:400])

print("SMS cross-check")
PANEL = "Dial *34488*1*PIN# or SMS PIN to 34488 for free"
sc, qual = agent._resolve_shortcode("38353", PANEL)
check("panel instructions overrule a misread shortcode",
      sc == "34488" and "I also read 38353" in qual,
      repr((sc, qual)))
sc, qual = agent._resolve_shortcode("34488", PANEL)
check("agreement needs no qualifier", sc == "34488" and qual == "",
      repr((sc, qual)))
sc, qual = agent._resolve_shortcode("", PANEL)
check("consistent panel fills a missing shortcode",
      sc == "34488" and qual == "", repr((sc, qual)))
sc, qual = agent._resolve_shortcode("", "Text PIN to 55667")
check("single mention fills with caution",
      sc == "55667" and "I only caught" in qual, repr((sc, qual)))
sc, qual = agent._resolve_shortcode("11111", "SMS 22222 then *33333*9# ok")
check("messy panel keeps the field with doubt",
      sc == "11111" and "I also read" in qual, repr((sc, qual)))
sc, qual = agent._resolve_shortcode("34488", "SMS 2062775840753")
check("long runs never become shortcodes",
      sc == "34488" and qual == "", repr((sc, qual)))
sc, qual = agent._resolve_shortcode("34488", "")
check("no instructions, no doubt", sc == "34488" and qual == "",
      repr((sc, qual)))

_real_extract4 = _vision.extract_from_image
try:
    _vision.extract_from_image = lambda *a, **k: _vision.Extraction(
        product_name="TadalisPlus", pin="2062775840753",
        shortcode="38353",
        verify_text="Dial *34488*1*PIN# or SMS PIN to 34488 for free")
    r = agent.chat("", [], b"fake-photo-bytes", "image/jpeg")
    check("photo SMS uses the panel shortcode, flags the misread",
          "Text 2062775840753 to 34488" in r["answer"]
          and "I also read 38353" in r["answer"],
          r["answer"][-400:])
finally:
    _vision.extract_from_image = _real_extract4

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
