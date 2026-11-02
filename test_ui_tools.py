"""UI wiring for the newer tools. Run: python test_ui_tools.py

Static checks over ui/index.html (the repo's established UI-test style):
the forwarded-list checker, the GS1 decoder, the ADR document button,
and the scan tallies must exist and call the endpoints that serve them.
No browser needed.
"""

from __future__ import annotations

import os

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


print("debunker")
check("list card exists",
      'id="debunkText"' in HTML and 'id="debunkGo"' in HTML
      and 'id="debunkOut"' in HTML)
check("list calls its endpoint",
      "$('#debunkGo').onclick" in HTML
      and "'/api/debunk'" in HTML)
check("counts rendered honestly",
      "in the register" in HTML and "unmatched, of" in HTML)

print("gs1 box")
check("pack-code box exists",
      'id="gs1Box"' in HTML and 'id="gs1Go"' in HTML
      and 'id="gs1Out"' in HTML)
check("box calls its endpoint",
      "$('#gs1Go').onclick" in HTML
      and "'/api/gs1/decode'" in HTML)
check("check-digit failure shown plainly",
      "CHECK FAILED" in HTML)

print("adr + scans")
check("reaction note offered per case",
      "'adr_note'" in HTML and "'Reaction note'" in HTML)
check("analytics shows scan tallies",
      "a.scans" in HTML and "packs checked" in HTML)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
