"""Agent stop-button wiring. Run: python test_ui_stop.py

Static checks over ui/index.html (the repo's established UI-test style):
while the agent is answering, Stop must be visible, abort the request,
say plainly that it stopped, and hand the input back for the next
message. No browser needed.
"""

from __future__ import annotations

import os
import re

fails = 0

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = open(os.path.join(HERE, "ui", "index.html"),
            encoding="utf-8").read()


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("stop control")
check("stop button exists, hidden until busy",
      '<button class="btn ghost sm hidden" id="agentStop" '
      'type="button">Stop</button>' in HTML,
      "agentStop button")
check("stop sits beside send",
      HTML.find('id="agentSend"') < HTML.find('id="agentStop"')
      < HTML.find('id="agentSend"') + 500)

print("abort wiring")
check("request carries an abort signal",
      "new AbortController()" in HTML
      and "signal:ctrl.signal" in HTML)
check("stop aborts the in-flight request",
      "$('#agentStop').onclick = agentStop" in HTML
      and "agentAbort.abort()" in HTML)
check("abort says stopped, not an error",
      "ctrl.signal.aborted ? 'Stopped.'" in HTML)
_abort_at = HTML.find("ctrl.signal.aborted ? 'Stopped.'")
check("stopped answers leave no assistant turn",
      _abort_at > 0
      and "agentHist.push" not in HTML[_abort_at:_abort_at + 300])
check("input returns for the next message",
      "input.focus();" in HTML)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
