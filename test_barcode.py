"""
Barcode rail tests: check digits, identity scoring, cache/offline honesty,
and partial source outages.

No network: live lookups are monkeypatched or switched off.

Run:  python test_barcode.py
"""

from __future__ import annotations

import os
import tempfile

TMP = tempfile.mkdtemp(prefix="vouch-barcode-")
os.environ["VOUCH_CACHE_DIR"] = TMP

import barcode as B

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("check digits")
check("valid EAN-13 passes", B.checksum_ok("4006381333931") is True)
check("mutated digit fails", B.checksum_ok("4006381333932") is False)
check("unknown length is unknown, not invalid",
      B.checksum_ok("12345") is None)
check("non-digits are unknown", B.checksum_ok("abcdefgh") is None)
check("digits_only strips separators",
      B.digits_only("4006 3813-33931") == "4006381333931")

print("identity scoring")
item = B._from_product(
    "4006381333931",
    {"product_name": "Cway Drinking Water", "brands": "Cway"},
    "openfoodfacts", "https://world.openfoodfacts.org")
check("empty pack input is unknown, never a mismatch",
      B.identity("", "", item)["verdict"] == "unknown")
check("brand plus descriptor matches",
      B.identity("Cway Drinking Water", "Cway", item)["verdict"] == "match")
check("different product flagged",
      B.identity("Pepsi Cola", "Pepsi", item)["verdict"] == "different")
check("scores stay in range",
      0.0 <= B.match_score("Cway", "Cway Drinking Water") <= 1.0)

print("offline honesty")
res = B.lookup("4006381333931", offline=True, use_fixtures=False)
check("offline cache miss is unavailable, not not-found",
      res.unavailable is True and res.found is False, res.message)
B._cache_put("4006381333931", item.to_dict())
res = B.lookup("4006381333931", offline=True, use_fixtures=False)
check("offline cache hit answers",
      res.found is True and res.cached is True)

print("partial outages")
_real_query = B._query


def _flaky(base: str, code: str):
    _flaky.calls.append(base)
    if len(_flaky.calls) == 1:
        raise B.BarcodeUnavailable("simulated outage")
    return None


_flaky.calls = []
B._query = _flaky
try:
    res = B.lookup("4006381333948", use_fixtures=False)
    check("partial outage is disclosed in signals",
          any("reach" in s for s in res.signals), str(res.signals))
    check("still reports not found", res.found is False)
finally:
    B._query = _real_query


def _down(base: str, code: str):
    raise B.BarcodeUnavailable("down")


B._query = _down
try:
    try:
        B.lookup("4006381333948", use_fixtures=False)
        check("total outage raises unavailable", False)
    except B.BarcodeUnavailable:
        check("total outage raises unavailable", True)
finally:
    B._query = _real_query

print("response caps")
import urllib.request as _ureq
_real_open = _ureq.urlopen


class _BigResp:
    def read(self, n=-1):
        return b"z" * (n if n is not None and n >= 0 else 10)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_ureq.urlopen = lambda *a, **k: _BigResp()  # noqa: E731
try:
    try:
        B._fetch("https://example.invalid/x")
        check("oversize reply refused", False)
    except B.BarcodeUnavailable as e:
        check("oversize reply refused", "too large" in str(e), str(e))
finally:
    _ureq.urlopen = _real_open

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
