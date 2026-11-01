"""
Seam tests: manufacturer hookup and SMS answers.

No network, no provider: the manufacturer seam is exercised unconfigured
and in demo mode; SMS answers come from a temporary mirror.

Run:  python test_seams.py
"""

from __future__ import annotations

import os
import tempfile
import time
import urllib.request

TMP = tempfile.mkdtemp(prefix="vouch-seams-")
os.environ["VOUCH_CACHE_DIR"] = TMP
os.environ.pop("VOUCH_MFR_URL", None)
os.environ["VOUCH_SMS_SECRET"] = "test-secret"

import manufacturer as M
import registry as R
import sms

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

    def read(self) -> bytes:
        return self._payload

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

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
