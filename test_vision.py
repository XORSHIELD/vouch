"""
Vision tests. The Meta path runs against a stubbed HTTP layer: no key, no
network. The text parser and fixtures run for real.

Run:  python test_vision.py
"""

from __future__ import annotations

import json
import sys

import vision

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


class _Resp:
    def __init__(self, status: int = 200, body: dict | None = None,
                 text: str = "") -> None:
        self.status_code = status
        self._body = body
        self.text = text or json.dumps(body or {})

    def json(self) -> dict:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body or {}


class _Requests:
    """Stub for the requests module. Each test sets _Requests.next."""
    next: object = None
    posted: dict = {}

    @classmethod
    def post(cls, url: str, headers: dict | None = None,
             json: dict | None = None, timeout: int = 0):  # noqa: A002
        cls.posted = {"url": url, "headers": headers,
                      "json": json, "timeout": timeout}
        if isinstance(cls.next, Exception):
            raise cls.next
        return cls.next


_real_requests = sys.modules.get("requests")
sys.modules["requests"] = _Requests  # type: ignore
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
    _Requests.next = _Resp(
        200, {"choices": [{"message": {"content": json.dumps(body)}}]})
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
          (_Requests.posted["headers"] or {}).get("Authorization")
          == "Bearer test-key")
    check("standard-tier model only",
          _Requests.posted["json"]["model"] == "muse-spark-1.3"
          and _Requests.posted["url"].endswith("/chat/completions"),
          str(_Requests.posted["json"].get("model")))

    body["legible"] = False
    _Requests.next = _Resp(
        200, {"choices": [{"message": {"content": json.dumps(body)}}]})
    ex = vision._extract_meta(b"x", "image/png")
    check("illegible lowers confidence",
          ex.confidence == 0.35
          and any("clearly" in w for w in ex.warnings),
          str(ex.warnings))

    print("meta failures stay honest")
    _Requests.next = _Resp(401, {}, "unauthorized")
    try:
        vision._extract_meta(b"x", "image/png")
        check("401 names the key", False)
    except vision.VisionUnavailable as exc:
        check("401 names the key", "401" in str(exc), str(exc))
    _Requests.next = _Resp(500, {}, "boom")
    try:
        vision._extract_meta(b"x", "image/png")
        check("500 carries the status", False)
    except vision.VisionUnavailable as exc:
        check("500 carries the status", "500" in str(exc), str(exc))
    _Requests.next = RuntimeError("dns down")
    try:
        vision._extract_meta(b"x", "image/png")
        check("network failure says so", False)
    except vision.VisionUnavailable as exc:
        check("network failure says so",
              "Could not reach" in str(exc), str(exc))
    _Requests.next = _Resp(
        200, {"choices": [{"message": {"content": "not json"}}]})
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
    if _real_requests is None:
        sys.modules.pop("requests", None)
    else:
        sys.modules["requests"] = _real_requests

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
