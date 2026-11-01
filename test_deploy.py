"""test_deploy.py: deployment guide accuracy.

Every concrete claim docs/deploy.md makes is checked against the repo:
environment names exist in code, commands name real files, the port
matches the server default, and CI installs requirements.txt and runs
the suite.

Run:  python test_deploy.py
"""

from __future__ import annotations

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
GUIDE = open(os.path.join(HERE, "docs", "deploy.md"), encoding="utf-8").read()
PY = ""
for _name in os.listdir(HERE):
    if _name.endswith(".py"):
        with open(os.path.join(HERE, _name), encoding="utf-8") as _fh:
            PY += _fh.read() + "\n"

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("guide structure")
for _s in ["## Install", "## Environment", "## TLS",
           "## Run as a service", "## Verify and operate",
           "## Launch checklist"]:
    check(f"section {_s}", _s in GUIDE)
for _tok in ["vouch.service", "systemctl", "nssm", "Task Scheduler",
             "reverse_proxy 127.0.0.1:8077", "requirements.txt",
             ".venv", "python -m venv .venv",
             "/api/health", "/app"]:
    check(f"mentions {_tok}", _tok in GUIDE)

print("env names are real")
_names = sorted(set(re.findall(r"VOUCH_[A-Z_]+", GUIDE)))
check("guide names environments", bool(_names))
for _n in _names:
    check(f"{_n} exists in code", _n in PY, _n)
for _m in ["MODEL_API_KEY", "META_API_KEY", "META_BASE",
           "META_VISION_MODEL"]:
    check(f"{_m} exists in code", _m in PY)

print("commands name real files")
_cmds = sorted(set(re.findall(r"python (\w[\w.-]*\.py)", GUIDE)))
check("guide names commands", bool(_cmds), str(_cmds))
for _f in _cmds:
    check(f"python {_f} exists",
          os.path.isfile(os.path.join(HERE, _f)), _f)
_m = re.search(r'VOUCH_PORT", "(\d+)"', PY)
check("server default port found", bool(_m))
if _m:
    check("guide uses the server port", _m.group(1) in GUIDE,
          _m.group(1))

print("install chain")
_reqs = [ln.split("#")[0].strip()
         for ln in open(os.path.join(HERE, "requirements.txt"),
                        encoding="utf-8")]
_reqs = [r for r in _reqs if r]
check("requirements listed", len(_reqs) >= 3, str(_reqs))
_EVIDENCE = {"pillow": "from PIL import", "httpx": "TestClient",
             "winrt-Windows.Graphics.Imaging":
                 "winrt.windows.graphics.imaging",
             "winrt-Windows.Media.Ocr": "winrt.windows.media.ocr",
             "winrt-Windows.Storage": "winrt.windows.storage"}
for _r in _reqs:
    _name = re.split(r"[;\s<>=!~]", _r, 1)[0].strip()
    _ev = _EVIDENCE.get(_name, f"import {_name}")
    _found = _ev in PY or f"from {_name} import" in PY
    check(f"{_name} is used", _found, _r)
_ci = open(os.path.join(HERE, ".github", "workflows", "ci.yml"),
           encoding="utf-8").read()
check("CI installs requirements.txt",
      "pip install -r requirements.txt" in _ci)
check("old inline pip line gone",
      "pip install fastapi uvicorn httpx pillow" not in _ci)
check("CI runs the tour test", "python test_tour.py" in _ci)
check("CI runs the i18n test", "python test_i18n.py" in _ci)
check("CI runs the voice test", "python test_voice.py" in _ci)
check("CI runs the batchdecode test", "python test_batchdecode.py" in _ci)
check("CI runs the deploy test", "python test_deploy.py" in _ci)
check("CI runs the server unit test",
      "python test_server_unit.py" in _ci)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
