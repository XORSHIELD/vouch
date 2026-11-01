"""
Vouch — manufacturer verification seam.

Only the maker can say whether a valid-looking serial is really theirs, so
this module is a plug-in point, not a verdict. With no partner configured it
says so honestly; with VOUCH_MFR_URL set it asks the partner's webhook.

Partner contract (HTTPS POST, JSON):
    -> {"manufacturer": str, "serial": str}
    <- {"verdict": "authentic" | "suspicious" | "unknown", "detail": str}

A "suspicious" answer is evidence for the engine, never a displayed verdict.
engine.assess() folds the answer into its checks when the pack carries a serial.
"""

from __future__ import annotations

import json
import os
import urllib.request

TIMEOUT = 15


def verify(manufacturer: str, serial: str) -> dict:
    """Ask the configured maker about one serial. Never raises for config."""
    manufacturer = (manufacturer or "").strip()
    serial = (serial or "").strip()
    if not serial:
        return {"connected": False, "verdict": "unknown",
                "detail": "No serial supplied."}
    url = os.environ.get("VOUCH_MFR_URL") or ""
    if not url:
        return {"connected": False, "verdict": "unknown",
                "detail": ("No manufacturer service is connected. "
                           "Set VOUCH_MFR_URL to enable serial checks.")}
    if os.environ.get("VOUCH_MFR_URL") == "demo":
        return {"connected": True, "verdict": "unknown",
                "detail": "Demo connector: echoes unknown.",
                "manufacturer": manufacturer, "serial": serial}
    if not (url.startswith("https://") or url.startswith("http://localhost")
            or url.startswith("http://127.0.0.1")):
        return {"connected": False, "verdict": "unknown",
                "detail": ("Refusing to send serials over unencrypted HTTP. "
                           "Use an https:// partner URL."),
                "manufacturer": manufacturer, "serial": serial}
    body = json.dumps({"manufacturer": manufacturer,
                       "serial": serial}).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "User-Agent": "Vouch/0.1 manufacturer-check"}
    key = os.environ.get("VOUCH_MFR_KEY") or ""
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"connected": True, "verdict": "unknown",
                "detail": f"The manufacturer service failed: {exc}",
                "manufacturer": manufacturer, "serial": serial}
    verdict = str(payload.get("verdict") or "unknown").lower()
    if verdict not in ("authentic", "suspicious", "unknown"):
        verdict = "unknown"
    return {"connected": True, "verdict": verdict,
            "detail": str(payload.get("detail") or "")[:500],
            "manufacturer": manufacturer, "serial": serial}
