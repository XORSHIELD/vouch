"""test_tour.py: first-run tour wiring.

Static checks over ui/index.html: every static id the tour script touches
exists in the markup, the step functions and boot hook are present, the
dynamic button ids appear in both their template and their wiring, and
the sample fixture key exists in fixtures/packs.json.

Run:  python test_tour.py
"""

from __future__ import annotations

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = open(os.path.join(HERE, "ui", "index.html"), encoding="utf-8").read()

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("tour markup")
for _id in ["tourModal", "tourTitle", "tourStep", "tourBody", "tourClose",
            "btnReplayTour"]:
    check(f"#{_id} exists", f'id="{_id}"' in HTML)
check("modal reuses the shell classes",
      'class="modal hidden" id="tourModal"' in HTML)

print("tour script")
for _fn in ["function tourWelcome", "function tourSample",
            "function tourYourTurn", "function maybeStartTour",
            "function tourDone", "function tourStep"]:
    check(f"{_fn} defined", _fn in HTML)
check("boot hook present", "maybeStartTour();" in HTML)
for _dyn in ["btnTourSample", "btnTourSkip", "btnTourOpen", "btnTourNext",
             "btnTourRetry", "btnTourGo", "btnTourEnd"]:
    check(f"{_dyn} templated and wired",
          HTML.count(_dyn) >= 2, str(HTML.count(_dyn)))
check("close box finishes the tour",
      bool(re.search(r"\$\('#tourClose'\)\.onclick = tourDone", HTML)))
check("click-outside finishes the tour",
      "e.target.id === 'tourModal'" in HTML)

print("tour fixture")
_m = re.search(r"TOUR_SAMPLE\s*=\s*['\"]([^'\"]+)['\"]", HTML)
check("sample key literal found", bool(_m))
_key = _m.group(1) if _m else ""
_packs = json.load(open(os.path.join(HERE, "fixtures", "packs.json"),
                        encoding="utf-8"))
check("sample fixture exists",
      any(p.get("key") == _key for p in _packs), _key)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
