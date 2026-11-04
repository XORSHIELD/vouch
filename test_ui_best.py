"""Best-rated UI bar: keyboard, touch, command palette, thumb bar.

Static checks over ui/index.html + ui/marketing.html (the repo's
established UI-test style). Covers the 2026 best-rated gaps: visible
keyboard focus, skip link, 44px touch targets, Cmd/Ctrl+K palette,
and a mobile thumb-reach action bar.

Run:  python test_ui_best.py
"""

from __future__ import annotations

import os
import re

fails = 0

HERE = os.path.dirname(os.path.abspath(__file__))
APP = open(os.path.join(HERE, "ui", "index.html"), encoding="utf-8").read()
MKT = open(os.path.join(HERE, "ui", "marketing.html"), encoding="utf-8").read()


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("keyboard")
check("app has a global :focus-visible rule",
      re.search(r"(^|\n)\s*:focus-visible\s*\{", APP) is not None,
      "add a global :focus-visible rule")
check("app has a skip link",
      'class="skip"' in APP and 'href="#main"' in APP, "skip link")
check("app main is the skip target",
      'id="main"' in APP, "main id")

print("touch targets")
check("theme toggle is 44px",
      re.search(r"\.theme-toggle\{[^}]*44px", APP) is not None,
      "theme-toggle 44px")
check("side toggle is 44px",
      re.search(r"\.side-toggle\{[^}]*44px", APP) is not None,
      "side-toggle 44px")
check("nav tabs meet 44px",
      re.search(r"nav\.tabs button[^{]*\{[^}]*min-height:\s*44px", APP)
      is not None, "nav.tabs min-height")
check("coarse-pointer touch floor",
      "pointer:coarse" in APP, "coarse-pointer block")

print("command palette")
check("palette markup exists",
      'id="cmdPalette"' in APP and 'id="cmdInput"' in APP, "cmdPalette")
check("palette opens on Cmd/Ctrl+K",
      'cmdPalette' in APP and "metaKey" in APP and "ctrlKey" in APP
      and ("'k'" in APP or '"k"' in APP), "Cmd/Ctrl+K")
check("palette drives tabs, scan, theme",
      "cmdPalette" in APP and "goTab(" in APP and "openScanner(" in APP
      and "setTheme(" in APP, "palette actions")
check("palette closes on Escape",
      "cmdClose" in APP and "Escape" in APP, "Esc closes palette")

print("thumb bar")
check("mobile thumb bar exists",
      'id="thumbBar"' in APP, "thumbBar")
check("thumb bar has Scan/Ask/Reports",
      'data-thumb="scan"' in APP and 'data-thumb="ask"' in APP
      and 'data-thumb="reports"' in APP, "thumb actions")
check("thumb bar is mobile-only",
      "thumbBar" in APP and "max-width" in APP, "mobile-only CSS")

print("marketing")
check("no render-blocking webfont",
      "fonts.googleapis.com" not in MKT, "drop Google Fonts for system stack")
check("marketing burger is 44px",
      re.search(r"\.burger\{[^}]*44px", MKT) is not None, "burger 44px")

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
