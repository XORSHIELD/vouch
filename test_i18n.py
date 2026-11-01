"""test_i18n.py: translation files complete and wired.

Every language carries exactly the English key set, no value is empty,
every English string appears on the page, and every data-i18n key the
page references exists.

Run:  python test_i18n.py
"""

from __future__ import annotations

import html
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
I18N = os.path.join(HERE, "ui", "i18n")
LANGS = ("en", "pcm", "ha", "yo", "ig")

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("translation files")
packs = {}
for _code in LANGS:
    with open(os.path.join(I18N, f"{_code}.json"),
              encoding="utf-8") as _fh:
        packs[_code] = json.load(_fh)
    check(f"{_code}.json loads", isinstance(packs[_code], dict))
_en_keys = set(packs["en"])
check("english has keys", bool(_en_keys), str(len(_en_keys)))
for _code in LANGS[1:]:
    _keys = set(packs[_code])
    check(f"{_code} matches the key set",
          _keys == _en_keys,
          f"missing={sorted(_en_keys - _keys)} extra={sorted(_keys - _en_keys)}")
for _code in LANGS:
    _empty = [k for k, v in packs[_code].items()
              if not isinstance(v, str) or not v.strip()]
    check(f"{_code} has no empty values", not _empty, str(_empty))

print("english strings exist")
with open(os.path.join(HERE, "ui", "index.html"),
          encoding="utf-8") as _fh:
    PAGE = _fh.read()
TEXT = html.unescape(PAGE)
for _k in sorted(_en_keys):
    check(f"en {_k} on page", packs["en"][_k] in TEXT, packs["en"][_k])

print("page keys wired")
refs = set(re.findall(r'data-i18n(?:-ph)?="([^"]+)"', PAGE))
check("page references keys", bool(refs), str(len(refs)))
for _k in sorted(refs):
    check(f"key {_k} exists", _k in _en_keys, _k)
for _fn in ["function t(", "async function loadLang(",
            "function applyLang(", "function bandLabel(",
            "function bandTag(", "function bandInfo(",
            "function currentLang("]:
    check(f"{_fn} defined", _fn in PAGE)
check("switcher applies the language", PAGE.count("applyLang()") >= 2,
      str(PAGE.count("applyLang()")))
check("band lookups use the key prefix",
      "'band.' + code" in PAGE and "'bandtag.' + code" in PAGE)
for _opt in ['<option value="pcm">Nigerian Pidgin</option>',
             '<option value="ha">Hausa</option>',
             '<option value="ig">Igbo</option>',
             '<option value="yo">Yorùbá</option>']:
    check(f"option live {_opt[15:18]}", _opt in PAGE, _opt)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
