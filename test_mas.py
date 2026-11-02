"""
Scratch-PIN tests: extraction, shortcodes, and the MAS seam.

No network, no provider: all answers come from local fixtures.

Run:  python test_mas.py
"""

from __future__ import annotations

import os
import urllib.request

os.environ.pop("VOUCH_MAS_URL", None)

import mas
import scan as S

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("pin extraction")
pins = S.live_pins("SCRATCH HERE\nPIN: 4829 1047 2213")
check("grouped PIN joins and is strong",
      pins == [{"value": "482910472213", "strength": "strong"}], str(pins))
pins = S.live_pins("PIN 4829-1047-2213")
check("dash-grouped PIN joins",
      pins == [{"value": "482910472213", "strength": "strong"}], str(pins))
pins = S.live_pins("CODE 7X2K9P")
check("short labelled code is strong",
      pins == [{"value": "7X2K9P", "strength": "strong"}], str(pins))

print("pin restraint")
check("batch is not a PIN", S.live_pins("BATCH NO: ACD1902") == [],
      str(S.live_pins("BATCH NO: ACD1902")))
check("printed barcode is not a PIN", S.live_pins("5010162000164") == [],
      str(S.live_pins("5010162000164")))
check("product words are not PINs",
      S.live_pins("PARACETAMOL 500MG TABLETS") == [])
check("dates are not PINs", S.live_pins("EXP 03/2027") == [])
check("NAFDAC numbers are not PINs", S.live_pins("NAFDAC A11-0551") == [],
      str(S.live_pins("NAFDAC A11-0551")))

print("shortcodes")
check("sms shortcode read",
      S.live_shortcode("TEXT PIN TO 38353") == "38353")
check("no shortcode, no guess", S.live_shortcode("no code here") == "")

print("mas seam")
out = mas.verify("", "")
check("empty PIN refused",
      out["verdict"] == "unknown" and "No PIN" in out["detail"], str(out))
out = mas.verify("482910472213", "38353")
check("unconfigured names the SMS route",
      out["connected"] is False and "38353" in out["detail"], str(out))
os.environ["VOUCH_MAS_URL"] = "demo"
out = mas.verify("482910472213", "38353")
check("demo connector echoes",
      out["connected"] is True and out["pin"] == "482910472213",
      str(out))
os.environ.pop("VOUCH_MAS_URL", None)

print("mas transport guards")
os.environ["VOUCH_MAS_URL"] = "http://mas.example/check"
out = mas.verify("482910472213", "38353")
check("plain http refused without dialing",
      out["connected"] is False and "https" in out["detail"].lower(),
      str(out))
os.environ.pop("VOUCH_MAS_URL", None)


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, n: int = -1) -> bytes:
        return self._payload[:n] if n is not None and n >= 0 else self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_real_urlopen = urllib.request.urlopen


def _fake_urlopen(req, timeout=None):
    return _FakeResp(b'{"verdict": "unknown", "detail": "' + b"y" * 2000
                     + b'"}')


urllib.request.urlopen = _fake_urlopen
try:
    os.environ["VOUCH_MAS_URL"] = "https://mas.example/check"
    out = mas.verify("482910472213", "38353")
    check("https provider reached",
          out["connected"] is True and out["pin"] == "482910472213",
          str(out))
    check("provider detail capped", len(out["detail"]) <= 500,
          str(len(out["detail"])))
    urllib.request.urlopen = lambda req, timeout=None: _FakeResp(b"z" * 300000)
    out = mas.verify("482910472213", "38353")
    check("oversize reply refused",
          out["verdict"] == "unknown" and "too large" in out["detail"],
          str(out)[:200])
finally:
    urllib.request.urlopen = _real_urlopen
    os.environ.pop("VOUCH_MAS_URL", None)
banned = ("fake", "counterfeit", "genuine", "authentic")
ours = [mas.verify("", "")["detail"],
        mas.verify("482910472213", "38353")["detail"]]
check("no binary language in our templates",
      not any(w in " ".join(ours).lower() for w in banned), " | ".join(ours))

print("reported shortcodes")
check("photographed code named",
      mas.describe_shortcode("34488") == "Sproxil")
check("reported code named",
      mas.describe_shortcode("38353") == "Sproxil"
      and mas.describe_shortcode("38351") == "PharmaSecure",
      repr(mas.REPORTED_SHORTCODES))
check("unknown code unnamed", mas.describe_shortcode("00000") == "")
check("reported stay out of photographed hints",
      "38353" not in mas.SHORTCODE_HINTS.values())
import agent as _agent
_hint = _agent._provider_hint()
check("photographed hint intact",
      "Sproxil panels usually show 34488" in _hint, _hint[:200])
check("reported hint labelled published",
      "38353" in _hint and "not yet photographed" in _hint,
      _hint[:300])

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
