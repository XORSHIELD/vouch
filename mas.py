"""
Vouch — scratch-PIN (MAS) verification seam.

Many packs carry a scratch panel: reveal the PIN, text it to the shortcode,
and the verification service answers. Vouch reads the PIN with the camera;
this module asks the service what the PIN says. With no provider configured
it says so honestly and tells the person how to check by SMS instead.

Provider contract (HTTPS POST, JSON):
    -> {"pin": str, "shortcode": str}
    <- {"verdict": "authentic" | "suspicious" | "unknown", "detail": str}

The provider's answer is quoted as its statement, with the provider named.
Vouch itself never declares a pack authentic: a "suspicious" answer is
evidence for the engine, and "unknown" means ask the shortcode directly.
"""

from __future__ import annotations

import json
import os
import urllib.request

TIMEOUT = 15

# Shortcodes confirmed from photographed panels. Hints only: guidance
# still tells the person to go by what is printed beside their panel,
# and only ever add a provider seen on a real pack.
SHORTCODE_HINTS = {"sproxil": "34488"}


def verify(pin: str, shortcode: str = "") -> dict:
    """Ask the configured service about one scratch PIN. Never raises."""
    pin = "".join((pin or "").split()).upper()
    shortcode = "".join((shortcode or "").split())
    if not pin:
        return {"connected": False, "verdict": "unknown",
                "detail": "No PIN supplied.", "pin": ""}
    url = os.environ.get("VOUCH_MAS_URL") or ""
    if not url:
        route = (f" Text {pin} to {shortcode}."
                 if shortcode else
                 " Text it to the shortcode printed beside the panel.")
        return {"connected": False, "verdict": "unknown",
                "detail": ("No verification service is connected."
                           + route),
                "pin": pin, "shortcode": shortcode,
                "sms": f"Text {pin} to {shortcode}" if shortcode else ""}
    if url == "demo":
        return {"connected": True, "verdict": "unknown",
                "detail": "Demo connector: echoes unknown.",
                "pin": pin, "shortcode": shortcode, "provider": "demo"}
    if not (url.startswith("https://") or url.startswith("http://localhost")
            or url.startswith("http://127.0.0.1")):
        return {"connected": False, "verdict": "unknown",
                "detail": ("Refusing to send PINs over unencrypted HTTP. "
                           "Use an https:// provider URL."),
                "pin": pin, "shortcode": shortcode}
    body = json.dumps({"pin": pin, "shortcode": shortcode}).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "User-Agent": "Vouch/0.4 mas-check"}
    key = os.environ.get("VOUCH_MAS_KEY") or ""
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=body, headers=headers)
    max_body = 256 * 1024
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(max_body + 1)
        if len(raw) > max_body:
            raise ValueError("provider reply too large")
        payload = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:
        return {"connected": True, "verdict": "unknown",
                "detail": f"The verification service failed: {exc}",
                "pin": pin, "shortcode": shortcode}
    verdict = str(payload.get("verdict") or "unknown").lower()
    if verdict not in ("authentic", "suspicious", "unknown"):
        verdict = "unknown"
    return {"connected": True, "verdict": verdict,
            "detail": str(payload.get("detail") or "")[:500],
            "pin": pin, "shortcode": shortcode,
            "provider": str(payload.get("provider") or url)}
