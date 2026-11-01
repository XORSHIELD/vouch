"""
Vouch — WhatsApp check-by-message seam.

Message-grade checks over the Meta WhatsApp Cloud API, reusing the SMS
seam's answer engine: same verdict vocabulary, same honesty rules.

Wiring (see docs/partner.md):
  VOUCH_WA_VERIFY      token Meta echoes back during webhook verification
  VOUCH_WA_APP_SECRET  Meta app secret; every POST must carry a valid
                       X-Hub-Signature-256 or it gets 403
  VOUCH_WA_TOKEN       API token used to send replies (demo = rehearse
                       without dialing Meta)
  VOUCH_WA_PHONE_ID    sender number id replies come from
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import urllib.request

import sms

GRAPH = "https://graph.facebook.com/v21.0"


def check_verify(mode: str, token: str, challenge: str) -> str:
    """Meta handshake: echo the challenge only on a token match."""
    want = os.environ.get("VOUCH_WA_VERIFY") or ""
    if (mode == "subscribe" and challenge and want
            and secrets.compare_digest(str(token or ""), want)):
        return challenge
    return ""


def check_signature(raw: bytes, header: str) -> bool:
    """X-Hub-Signature-256 over the exact raw body. Secure by default:
    an unconfigured app secret refuses everything."""
    secret = os.environ.get("VOUCH_WA_APP_SECRET") or ""
    if not secret or not (header or "").startswith("sha256="):
        return False
    mac = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + mac, header)


def parse_webhook(payload: dict) -> list[tuple[str, str | None]]:
    """(sender, text) pairs from a Meta webhook payload. Non-text
    messages yield None text; statuses and echoes yield nothing."""
    out: list[tuple[str, str | None]] = []
    if not isinstance(payload, dict):
        return out
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            value = (change or {}).get("value") or {}
            if value.get("messaging_product") != "whatsapp":
                continue
            for msg in value.get("messages") or []:
                if not isinstance(msg, dict):
                    continue
                text = (msg.get("text") or {}).get("body")
                out.append((msg.get("from", ""),
                            text if isinstance(text, str) else None))
    return out


def handle_inbound(text: str | None, sender: str = "") -> str:
    """One message in, one short answer out. Never raises."""
    if not text:
        return ("Vouch: send the NAFDAC number or barcode as text — "
                "photos can't be read here yet.")
    return sms.handle_inbound(text, sender)


def send_text(to: str, text: str) -> dict:
    """Send one text reply via the Graph API. Honest when unconfigured."""
    token = os.environ.get("VOUCH_WA_TOKEN") or ""
    phone_id = os.environ.get("VOUCH_WA_PHONE_ID") or ""
    if not token or not phone_id:
        return {"sent": False, "to": to, "reason": "unconfigured"}
    if token == "demo":
        return {"sent": True, "to": to, "id": "demo-no-send"}
    body = json.dumps({
        "messaging_product": "whatsapp", "to": to, "type": "text",
        "text": {"body": text[:4000]},
    }).encode()
    req = urllib.request.Request(
        f"{GRAPH}/{phone_id}/messages", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            out = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"sent": False, "to": to, "reason": f"send failed: {exc}"}
    if not isinstance(out, dict):
        return {"sent": False, "to": to, "reason": "send failed: bad reply"}
    _msgs = out.get("messages") or [{}]
    return {"sent": True, "to": to, "id": _msgs[0].get("id", "")}


def handle_post(raw: bytes, signature: str) -> tuple[int, dict]:
    """Whole POST in one sync call: (status, body). Never raises."""
    if not check_signature(raw, signature):
        return 403, {"detail": "Bad signature."}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError:
        return 400, {"detail": "Not JSON."}
    replies = []
    for sender, text in parse_webhook(payload):
        reply = handle_inbound(text, sender)
        sent = send_text(sender, reply) if sender else {"sent": False}
        replies.append({"to": sender, "reply": reply,
                        "sent": bool(sent.get("sent"))})
    return 200, {"replies": replies}
