"""
Explanation-layer tests: guardrails, deterministic fallback, transport
guard, and capped model replies.

No network: model calls are refused or faked.

Run:  python test_ai.py
"""

from __future__ import annotations

import json
import os
import urllib.request

os.environ.pop("MODEL_API_KEY", None)
os.environ.pop("META_API_KEY", None)

import ai
from engine import Assessment

ai.API_KEY = ""

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


def _a() -> Assessment:
    return Assessment(band="review", headline="Registration found",
                      checks=[], actions=[], score=1.5, registry=None,
                      input={})


CLEAN = ("The registration was found in the public register. Keep the pack "
         "and the receipt until the follow-up steps below are complete.")


print("violations")
A = _a()
check("banned verdict refused",
      bool(ai.violations("This medicine is fake, throw it away now.", A)))
check("guarantee refused",
      bool(ai.violations("This pack is guaranteed safe, no need to check.", A)))
check("dosage advice refused",
      bool(ai.violations(
          "You should take 2 tablets twice daily for a week, then rest.", A)))
check("invented number refused",
      bool(ai.violations(
          "Please also confirm number A99-0000 against the register before "
          "you decide anything today, thank you.", A)))
check("too short refused", bool(ai.violations("ok", A)))
check("too long refused", bool(ai.violations("x" * 2000, A)))
check("clean text passes", ai.violations(CLEAN, A) == [],
      str(ai.violations(CLEAN, A)))

print("fallback")
text, who = ai.compose(A)
check("no key means composer",
      who == "vouch-composer" and len(text) >= 80, f"{who} {len(text)}")
text2, who2 = ai.compose(A, "is it ok?")
check("questions answered too", who2 == "vouch-composer" and bool(text2))
chunks = list(ai.stream(A, delay=0))
check("stream ends done",
      chunks[-1].get("done") is True
      and chunks[0].get("meta", {}).get("provider") == "vouch-composer")
check("stream text reassembles",
      "".join(c.get("delta", "") for c in chunks) == text)

print("transport guard")
_old_key, _old_base = ai.API_KEY, ai.API_BASE
ai.API_KEY, ai.API_BASE = "k", "http://example.com"
try:
    ai.model_text(A)
    check("http base refused", False)
except RuntimeError as e:
    check("http base refused", "unencrypted" in str(e), str(e))
finally:
    ai.API_KEY, ai.API_BASE = _old_key, _old_base


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, n: int = -1) -> bytes:
        return self._payload[:n] if n is not None and n >= 0 else self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _canned(content: str) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"content": content}}]}).encode()


_real_urlopen = urllib.request.urlopen
ai.API_KEY, ai.API_BASE = "k", "http://127.0.0.1:9"
try:
    urllib.request.urlopen = lambda *a, **k: _FakeResp(_canned(CLEAN))
    check("localhost model answers",
          ai.model_text(A) == CLEAN)
    urllib.request.urlopen = lambda *a, **k: _FakeResp(_canned(
        "This medicine is fake, throw it away now, do not keep it."))
    try:
        ai.model_text(A)
        check("violating reply refused", False)
    except RuntimeError as e:
        check("violating reply refused", "guard:" in str(e), str(e))
    text, who = ai.compose(A)
    check("violations fall back to the composer",
          who == "vouch-composer", who)
    urllib.request.urlopen = lambda *a, **k: _FakeResp(b"y" * 300000)
    try:
        ai.model_text(A)
        check("oversize reply refused", False)
    except RuntimeError as e:
        check("oversize reply refused", "too large" in str(e), str(e))
finally:
    urllib.request.urlopen = _real_urlopen
    ai.API_KEY, ai.API_BASE = "", _old_base

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
