"""
Vision tests. The Meta path runs against a stubbed HTTP layer: no key, no
network. The text parser and fixtures run for real.

Run:  python test_vision.py
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import vision

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


class _FakeResp:
    """Stub for a urlopen response. Each test sets _FakeResp.next."""

    next: object = None
    posted: dict = {}

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, n: int = -1) -> bytes:
        return self._payload[:n] if n is not None and n >= 0 else self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(req, timeout=None):
    _FakeResp.posted = {"url": req.full_url,
                        "headers": dict(req.header_items()),
                        "json": json.loads(req.data.decode()),
                        "timeout": timeout}
    if isinstance(_FakeResp.next, Exception):
        raise _FakeResp.next
    return _FakeResp(_FakeResp.next)


def _canned(content: str) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"content": content}}]}).encode()


_real_urlopen = urllib.request.urlopen
urllib.request.urlopen = _fake_urlopen
_old_key = vision.API_KEY
vision.API_KEY = "test-key"

try:
    print("meta transcription")
    body = {"product_name": "AC-DREX Tablet", "nafdac_number": "A11-0551",
            "barcode": "5012345678900",
            "manufacturer": "Manufactured by A.C. Drugs Ltd",
            "batch": "EMZ7781", "exp_date": "EXP 03/2027",
            "mfg_date": "MFG 04/2024", "best_before": "",
            "pin": "abcd 1234", "shortcode": "1393 ext",
            "strength": "500 mg", "pack_size": "1 x 100",
            "legible": True, "notes": ""}
    _FakeResp.next = _canned(json.dumps(body))
    ex = vision._extract_meta(b"fake-bytes", "image/png")
    check("fields transcribed",
          ex.nafdac_number == "A11-0551" and ex.batch == "EMZ7781"
          and ex.product_name == "AC-DREX Tablet",
          str(ex.to_dict())[:300])
    check("pin normalised", ex.pin == "ABCD1234", ex.pin)
    check("maker prefix stripped", ex.manufacturer == "A.C. Drugs Ltd",
          ex.manufacturer)
    check("shortcode digits only", ex.shortcode == "1393", ex.shortcode)
    check("provider names the model",
          ex.provider == "meta:" + vision.MODEL, ex.provider)
    check("confident when legible", ex.confidence == 0.85,
          str(ex.confidence))
    check("key sent as bearer",
          (_FakeResp.posted["headers"] or {}).get("Authorization")
          == "Bearer test-key")
    check("standard-tier model only",
          _FakeResp.posted["json"]["model"] == "muse-spark-1.3"
          and _FakeResp.posted["url"].endswith("/chat/completions"),
          str(_FakeResp.posted["json"].get("model")))

    body["legible"] = False
    _FakeResp.next = _canned(json.dumps(body))
    ex = vision._extract_meta(b"x", "image/png")
    check("illegible lowers confidence",
          ex.confidence == 0.35
          and any("clearly" in w for w in ex.warnings),
          str(ex.warnings))

    print("meta failures stay honest")
    _FakeResp.next = urllib.error.HTTPError(
        "http://127.0.0.1:9/x", 401, "Unauthorized", {}, None)
    try:
        vision._extract_meta(b"x", "image/png")
        check("401 names the key", False)
    except vision.VisionUnavailable as exc:
        check("401 names the key", "401" in str(exc), str(exc))
    _FakeResp.next = urllib.error.HTTPError(
        "http://127.0.0.1:9/x", 500, "boom", {}, io.BytesIO(b"boom"))
    try:
        vision._extract_meta(b"x", "image/png")
        check("500 carries the status", False)
    except vision.VisionUnavailable as exc:
        check("500 carries the status", "500" in str(exc), str(exc))
    _FakeResp.next = RuntimeError("dns down")
    try:
        vision._extract_meta(b"x", "image/png")
        check("network failure says so", False)
    except vision.VisionUnavailable as exc:
        check("network failure says so",
              "Could not reach" in str(exc), str(exc))
    _FakeResp.next = _canned("not json")
    try:
        vision._extract_meta(b"x", "image/png")
        check("bad payload says so", False)
    except vision.VisionUnavailable as exc:
        check("bad payload says so",
              "Could not parse" in str(exc), str(exc))
    vision.API_KEY = ""
    try:
        vision._extract_meta(b"x", "image/png")
        check("no key says so", False)
    except vision.VisionUnavailable as exc:
        check("no key says so", "MODEL_API_KEY" in str(exc), str(exc))
    vision.API_KEY = "test-key"

    print("label parser")
    label = ("AC-DREX Tablet\nNAFDAC Reg No A11-0551\nBatch EMZ7781\n"
             "EXP 03/2027\nMFG 04/2024\nManufactured by A.C. Drugs Ltd\n"
             "500 mg\n1 x 100 Tablets")
    ex = vision.parse_label_text(label)
    check("parser finds the number",
          ex.nafdac_number == "A11-0551", ex.nafdac_number)
    check("parser finds batch + dates",
          ex.batch == "EMZ7781" and "2027" in ex.exp_date
          and "2024" in ex.mfg_date,
          f"{ex.batch} {ex.exp_date} {ex.mfg_date}")
    check("parser finds maker + strength",
          "A.C. Drugs" in ex.manufacturer and ex.strength == "500 MG",
          f"{ex.manufacturer} {ex.strength}")
    try:
        vision.parse_label_text("   ")
        check("empty text refused", False)
    except vision.VisionUnavailable:
        check("empty text refused", True)

    print("ocr-hardened parser")
    ex = vision.parse_label_text(
        "AC-Drex Tablet\nA110551\nBatch: ACD2401\nEXP 06/2028\n",
        confirm=lambda c: c == "A110551")
    check("dashless number confirmed, not guessed",
          ex.nafdac_number == "A110551", ex.nafdac_number)
    ex = vision.parse_label_text(
        "AC-Drex Tablet\nA110551\nEXP 06/2028\n",
        confirm=lambda c: False)
    check("unconfirmed dashless number refused",
          ex.nafdac_number == ""
          and any("No NAFDAC number" in w for w in ex.warnings),
          ex.nafdac_number)
    ex = vision.parse_label_text("AC-Drex Tablet\nA11-0551\nEXP 06/2028\n")
    check("dashed standalone number accepted",
          ex.nafdac_number == "A11-0551", ex.nafdac_number)
    ex = vision.parse_label_text("AC-Drex Tablet\nEXP 06/2028\n")
    check("mfg never steals the exp date", ex.mfg_date == "", ex.mfg_date)
    ex = vision.parse_label_text("AC-Drex Tablet\nMFG 06/2025\n")
    check("exp never steals the mfg date", ex.exp_date == "", ex.exp_date)
    ex = vision.parse_label_text("k \x07 Drex Tablet\nEXP 06/2028\n")
    check("control characters stripped",
          "\x07" not in ex.product_name and "\x07" not in ex.raw_text,
          repr(ex.product_name))
    dirty = dict(body, product_name="AC-DREX\x07 Tablet")
    _FakeResp.next = _canned(json.dumps(dirty))
    ex = vision._extract_meta(b"x", "image/png")
    check("model control characters stripped",
          "\x07" not in ex.product_name, repr(ex.product_name))

    print("fixtures")
    ex = vision.from_fixture("genuine")
    check("fixture loads",
          ex.provider == "fixture" and bool(ex.nafdac_number),
          ex.provider)
    try:
        vision.from_fixture("nope")
        check("unknown fixture refused", False)
    except vision.VisionUnavailable:
        check("unknown fixture refused", True)
finally:
    vision.API_KEY = _old_key
    urllib.request.urlopen = _real_urlopen

print("photo variants")
import visval

with open("samples/img/sample-genuine.png", "rb") as fh:
    raw = fh.read()
vs = visval.variants(raw)
check("four degradations",
      [k for k, _ in vs] == ["rotate12", "blur2", "noise", "flat"],
      str([k for k, _ in vs]))
check("variants are valid PNGs",
      all(v[:8] == b"\x89PNG\r\n\x1a\n" for _, v in vs))
check("variants differ", len({v for _, v in vs}) == 4)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
