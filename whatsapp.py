"""
Vouch — WhatsApp check-by-message seam.

Message-grade checks over the Meta WhatsApp Cloud API, reusing the SMS
seam's answer engine: same verdict vocabulary, same honesty rules.
Photos are fetched from Meta and read through the same vision pipeline
as the app; numbers found on them are answered by the SMS engine.

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

import mas
import sms
import vision

GRAPH = "https://graph.facebook.com/v21.0"

MAX_MEDIA = 5 * 1024 * 1024  # largest WhatsApp photo we download


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


def parse_messages(payload: dict) -> list[dict]:
    """Sender, text and image ids from a Meta webhook payload.

    Non-text messages carry None text; image messages also carry
    their media id. Statuses and echoes yield nothing.
    """
    out: list[dict] = []
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
                img = msg.get("image") or {}
                if not isinstance(img, dict):
                    img = {}
                out.append({
                    "sender": msg.get("from", ""),
                    "text": text if isinstance(text, str) else None,
                    "media_id": img.get("id", ""),
                    "mime": img.get("mime_type", ""),
                })
    return out


def parse_webhook(payload: dict) -> list[tuple[str, str | None]]:
    """(sender, text) pairs from a Meta webhook payload. Non-text
    messages yield None text; statuses and echoes yield nothing."""
    return [(m["sender"], m["text"]) for m in parse_messages(payload)]


def handle_inbound(text: str | None, sender: str = "") -> str:
    """One message in, one short answer out. Never raises."""
    if not text:
        return ("Vouch: send the NAFDAC number or barcode as text — "
                "this message type can't be read here.")
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


def fetch_media(media_id: str) -> dict:
    """Download one WhatsApp image via the Graph API. Never raises."""
    token = os.environ.get("VOUCH_WA_TOKEN") or ""
    if not token:
        return {"ok": False, "reason": "unconfigured"}
    if token == "demo":
        return {"ok": False, "reason": "demo"}
    if not media_id:
        return {"ok": False, "reason": "no media id"}
    try:
        req = urllib.request.Request(
            f"{GRAPH}/{media_id}",
            headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            meta = json.loads(resp.read().decode("utf-8", "replace"))
        if not isinstance(meta, dict):
            return {"ok": False, "reason": "fetch failed: bad reply"}
        url = meta.get("url") or ""
        if not url.startswith("https://"):
            return {"ok": False, "reason": "refusing plain-http media url"}
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read(MAX_MEDIA + 1)
        if len(data) > MAX_MEDIA:
            return {"ok": False, "reason": "media too large"}
    except Exception as exc:
        return {"ok": False, "reason": f"fetch failed: {exc}"}
    return {"ok": True, "bytes": data,
            "mime": meta.get("mime_type") or "image/jpeg"}


def handle_photo(media_id: str, mime: str = "") -> str:
    """One photo in, one short answer out. Never raises."""
    data = fetch_media(media_id or "")
    if not data.get("ok"):
        reason = data.get("reason", "")
        if reason == "demo":
            return ("Vouch: demo mode can't fetch photos — send the "
                    "NAFDAC number or barcode as text.")
        if reason == "unconfigured":
            return ("Vouch: photo checks aren't set up on this number — "
                    "send the NAFDAC number or barcode as text.")
        return ("Vouch: I couldn't fetch that photo — send the NAFDAC "
                "number or barcode as text.")
    try:
        ex = vision.extract_from_image(
            data["bytes"], mime or data.get("mime") or "image/jpeg")
    except vision.VisionUnavailable as exc:
        return f"Vouch: {exc} Or type the NAFDAC number as text."
    except Exception:
        return ("Vouch: that photo didn't read — type the NAFDAC number "
                "or barcode as text.")
    if ex.nafdac_number:
        return sms.handle_inbound(f"NAFDAC {ex.nafdac_number}")
    if ex.pin:
        res = mas.verify(ex.pin, ex.shortcode or "")
        return "Vouch: " + (res.get("detail")
                            or "that PIN needs the shortcode beside it.")
    return ("Vouch: no NAFDAC number or PIN came through on that photo "
            "— type the number as text and I'll check it.")


def handle_post(raw: bytes, signature: str) -> tuple[int, dict]:
    """Whole POST in one sync call: (status, body). Never raises."""
    if not check_signature(raw, signature):
        return 403, {"detail": "Bad signature."}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError:
        return 400, {"detail": "Not JSON."}
    replies = []
    for m in parse_messages(payload):
        sender, text = m["sender"], m["text"]
        if text:
            reply = handle_inbound(text, sender)
        elif m["media_id"]:
            reply = handle_photo(m["media_id"], m["mime"])
        else:
            reply = handle_inbound(None, sender)
        sent = send_text(sender, reply) if sender else {"sent": False}
        replies.append({"to": sender, "reply": reply,
                        "sent": bool(sent.get("sent"))})
    return 200, {"replies": replies}
