"""
Seam tests: manufacturer hookup and SMS answers.

No network, no provider: the manufacturer seam is exercised unconfigured
and in demo mode; SMS answers come from a temporary mirror.

Run:  python test_seams.py
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.request

TMP = tempfile.mkdtemp(prefix="vouch-seams-")
os.environ["VOUCH_CACHE_DIR"] = TMP
os.environ.pop("VOUCH_MFR_URL", None)
os.environ["VOUCH_SMS_SECRET"] = "test-secret"
os.environ["VOUCH_WA_VERIFY"] = "wa-verify"
os.environ["VOUCH_WA_APP_SECRET"] = "wa-secret"
os.environ["VOUCH_WA_PHONE_ID"] = "12345"
os.environ.pop("VOUCH_WA_TOKEN", None)

import manufacturer as M
import registry as R
import sms
import whatsapp

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("manufacturer seam")
out = M.verify("ACME Ltd", "SN-1")
check("unconfigured says so",
      out["connected"] is False and out["verdict"] == "unknown", str(out))
check("empty serial refused",
      M.verify("ACME Ltd", "")["verdict"] == "unknown")
os.environ["VOUCH_MFR_URL"] = "demo"
out = M.verify("ACME Ltd", "SN-1")
check("demo connector echoes",
      out["connected"] is True and out["serial"] == "SN-1", str(out))
os.environ.pop("VOUCH_MFR_URL", None)

print("manufacturer transport guards")
os.environ["VOUCH_MFR_URL"] = "http://partner.example/check"
out = M.verify("ACME Ltd", "SN-1")
check("plain http refused without dialing",
      out["connected"] is False and "https" in out["detail"].lower(),
      str(out))
os.environ.pop("VOUCH_MFR_URL", None)


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, n: int = -1) -> bytes:
        return self._payload[:n] if n is not None and n >= 0 else self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


dialed: list[str] = []
_real_urlopen = urllib.request.urlopen


def _fake_urlopen(req, timeout=None):
    dialed.append(req.full_url)
    return _FakeResp(b'{"verdict": "suspicious", "detail": "' + b"x" * 2000
                     + b'"}')


urllib.request.urlopen = _fake_urlopen
try:
    os.environ["VOUCH_MFR_URL"] = "https://partner.example/check"
    out = M.verify("ACME Ltd", "SN-1")
    check("https partner reached",
          out["connected"] is True and out["verdict"] == "suspicious"
          and dialed == ["https://partner.example/check"], str(out))
    check("partner detail capped", len(out["detail"]) <= 500,
          str(len(out["detail"])))
finally:
    urllib.request.urlopen = _real_urlopen
    os.environ.pop("VOUCH_MFR_URL", None)

print("sms seam")
con = R._mirror_db()
R._mirror_upsert(con, {"product_id": 7, "NAFDAC": "A11-0551",
                       "product_name": "AC-DREX Tablet",
                       "applicant_name": "A.C. Drugs Ltd",
                       "status": "Active"}, 1)
con.commit()
con.close()
R._sync_put(status="complete", finished_at=time.time(), total=1,
            fetched=1, pass_no=1, error="")
check("secret gates the webhook",
      sms.check_secret("test-secret") is True
      and sms.check_secret("wrong") is False)
reply = sms.handle_inbound("NAFDAC A11-0551")
check("registered number answered",
      "registered" in reply.lower() and "A.C. Drugs" in reply, reply)
reply = sms.handle_inbound("nafdac a99-9999")
check("unknown number is honest, not an accusation",
      "no record" in reply.lower(), reply)
reply = sms.handle_inbound("hello?")
check("gibberish gets usage", "NAFDAC <number>" in reply, reply)
reply = sms.handle_inbound("V 5012345678901")
check("bad check digit caught", "check digit" in reply.lower(), reply)
banned = ("fake", "counterfeit", "genuine", "authentic", "safe to")
texts = [sms.handle_inbound(t) for t in
         ("NAFDAC A11-0551", "NAFDAC A99-9999", "V 5012345678901",
          "hello?")]
check("no binary language in replies",
      not any(w in " ".join(texts).lower() for w in banned),
      " | ".join(texts))

print("whatsapp seam")
check("verify echoes on token match",
      whatsapp.check_verify("subscribe", "wa-verify", "CH9") == "CH9")
check("verify refuses wrong token",
      whatsapp.check_verify("subscribe", "nope", "CH9") == "")
check("verify refuses wrong mode",
      whatsapp.check_verify("unsubscribe", "wa-verify", "CH9") == "")
_payload = {"entry": [{"changes": [
    {"value": {"messaging_product": "whatsapp", "messages": [
        {"from": "2348012345678", "type": "text",
         "text": {"body": "NAFDAC A11-0551"}},
        {"from": "2348012345678", "type": "image",
         "image": {"id": "img1"}}]}},
    {"value": {"messaging_product": "whatsapp",
               "statuses": [{"id": "s1"}]}}]}]}
_msgs = whatsapp.parse_webhook(_payload)
check("text parsed, statuses skipped",
      _msgs == [("2348012345678", "NAFDAC A11-0551"),
                ("2348012345678", None)], str(_msgs))
check("garbage parses to nothing",
      whatsapp.parse_webhook({}) == []
      and whatsapp.parse_webhook({"entry": [{"changes": []}]}) == [])
reply = whatsapp.handle_inbound("NAFDAC A11-0551")
check("registered number answered",
      "registered" in reply.lower() and "A.C. Drugs" in reply, reply)
check("photos get a text-only note",
      "as text" in whatsapp.handle_inbound(None).lower())
import hashlib as _hl
import hmac as _hm
_raw = b'{"x": 1}'
_sig = "sha256=" + _hm.new(b"wa-secret", _raw, _hl.sha256).hexdigest()
check("valid signature accepted",
      whatsapp.check_signature(_raw, _sig) is True)
check("forged signature refused",
      whatsapp.check_signature(_raw, "sha256=" + "0" * 64) is False
      and whatsapp.check_signature(_raw, "") is False)
os.environ.pop("VOUCH_WA_APP_SECRET", None)
check("missing secret refuses everything",
      whatsapp.check_signature(_raw, _sig) is False)
os.environ["VOUCH_WA_APP_SECRET"] = "wa-secret"
check("send unconfigured says so",
      whatsapp.send_text("2348012345678", "hi")["sent"] is False)
os.environ["VOUCH_WA_TOKEN"] = "demo"
_n = len(dialed)
check("demo send dials nothing",
      whatsapp.send_text("2348012345678", "hi").get("sent") is True
      and len(dialed) == _n, str(dialed))
os.environ["VOUCH_WA_TOKEN"] = "real-token"
_sent: dict = {}


def _fake_wa(req, timeout=None):
    _sent["url"] = req.full_url
    _sent["auth"] = req.get_header("Authorization")
    _sent["body"] = json.loads(req.data.decode())
    return _FakeResp(b'{"messages": [{"id": "wamid.1"}]}')


urllib.request.urlopen = _fake_wa
try:
    out = whatsapp.send_text("2348012345678", "Vouch: hi")
finally:
    urllib.request.urlopen = _real_urlopen
check("graph api reached with bearer",
      out.get("sent") is True and out.get("id") == "wamid.1"
      and _sent["url"] == "https://graph.facebook.com/v21.0/12345/messages"
      and _sent["auth"] == "Bearer real-token", str(_sent))
check("message body shaped for whatsapp",
      _sent["body"].get("messaging_product") == "whatsapp"
      and _sent["body"].get("to") == "2348012345678",
      str(_sent["body"]))
os.environ.pop("VOUCH_WA_TOKEN", None)

print("whatsapp posts")
os.environ["VOUCH_WA_TOKEN"] = "demo"
_payload2 = {"entry": [{"changes": [{"value": {
    "messaging_product": "whatsapp", "messages": [
        {"from": "2348012345678", "type": "text",
         "text": {"body": "NAFDAC A11-0551"}}]}}]}]}
_raw2 = json.dumps(_payload2).encode()
_sig2 = "sha256=" + _hm.new(b"wa-secret", _raw2, _hl.sha256).hexdigest()
_st, _bd = whatsapp.handle_post(_raw2, _sig2)
check("signed post answered",
      _st == 200 and len(_bd["replies"]) == 1
      and "registered" in _bd["replies"][0]["reply"].lower()
      and _bd["replies"][0]["sent"] is True, str(_bd)[:200])
check("unsigned post refused",
      whatsapp.handle_post(_raw2, "")[0] == 403)
check("bad json refused",
      whatsapp.handle_post(b"nope{", _sig2)[0] in (400, 403))
_statuses = json.dumps(
    {"entry": [{"changes": [{"value": {
        "messaging_product": "whatsapp",
        "statuses": [{"id": "s1"}]}}]}]}).encode()
_sig3 = "sha256=" + _hm.new(b"wa-secret", _statuses, _hl.sha256).hexdigest()
_st3, _bd3 = whatsapp.handle_post(_statuses, _sig3)
check("status-only post ok with no replies",
      (_st3, _bd3) == (200, {"replies": []}), str((_st3, _bd3)))
os.environ.pop("VOUCH_WA_TOKEN", None)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
