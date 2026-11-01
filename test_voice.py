"""test_voice.py: hands-free voice conversation wiring.

Static checks over ui/index.html: the auto-listen loop, the meter-based
end-of-speech cut, and every path that ends hands-free mode exist and
are connected.

Run:  python test_voice.py
"""

from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = open(os.path.join(HERE, "ui", "index.html"), encoding="utf-8").read()

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("hands-free loop")
check("auto-listen defined", "function voiceAutoListen(){" in HTML)
check("auto-listen guards its preconditions",
      "if (!voiceHandsFree || !voiceOpen || voiceListening" in HTML)
check("speech end resumes listening",
      HTML.count("if (voiceHandsFree) voiceAutoListen();") >= 2,
      str(HTML.count("if (voiceHandsFree) voiceAutoListen();")))
check("muted answers resume listening",
      "if (voiceMuted) voiceAutoListen();" in HTML)
check("listening engages hands-free",
      "voiceHandsFree = true;" in HTML)

print("speech detection")
for _v in ["voiceVadCut", "voiceLastVoice", "voiceHeardLevel",
           "voiceRetries"]:
    check(f"state {_v} declared", f"let {_v}" in HTML)
check("start of speech shown", "Hearing you" in HTML)
check("silence cuts the turn",
      "voiceLastVoice > 0 && _now - voiceLastVoice > 1500" in HTML)
check("silent turns retry with a cap", "voiceRetries < 2" in HTML)

print("loop exits")
check("manual stop exits", HTML.count("voiceHandsFree = false;") >= 3,
      str(HTML.count("voiceHandsFree = false;")))
check("vad-cut turns do not loop",
      "voiceHandsFree && !voiceVadCut && voiceRetries < 2" in HTML)

print("agent mic")
check("agent speak-back defined", "function agentSpeak(" in HTML)
check("final result triggers a voice reply",
      "agentVoiceReply = true;" in HTML)
check("voice reply spoken after send", "if (agentVoiceReply)" in HTML)
check("mic errors speak up", "function agentMicErr(" in HTML)
check("secure context explained", "use localhost or HTTPS" in HTML)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
