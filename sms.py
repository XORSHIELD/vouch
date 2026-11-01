"""
Vouch — SMS/USSD seam.

Most counterfeit buyers never open an app. This module answers short text
queries ("NAFDAC A11-0551", "V 5010162000164") with short text answers, using
the same verdict vocabulary as the engine: states, never fake/genuine claims.

Transport is a provider webhook away: point the SMS provider at
POST /api/sms/inbound with {"sender", "text", "secret"} and set
VOUCH_SMS_SECRET to the shared value. Outbound delivery stays with the
provider; Vouch returns the reply text.
"""

from __future__ import annotations

import os
import re
import secrets

import barcode as B
import registry as R


def check_secret(secret: str) -> bool:
    want = os.environ.get("VOUCH_SMS_SECRET") or ""
    return bool(want) and secrets.compare_digest(str(secret or ""), want)


def handle_inbound(text: str, sender: str = "") -> str:
    """One text query in, one short answer out. Never raises."""
    try:
        return _answer((text or "").strip())
    except R.RegistryUnavailable:
        return ("Vouch: the register could not be reached. Try again later;"
                " an outage is not a result.")
    except Exception:
        return "Vouch: send NAFDAC <number> or V <barcode>."


def _answer(text: str) -> str:
    m = re.search(r"([A-Z]{0,2}\s?\d{1,2}\s?-?\s?\d{3,7})",
                  text.upper().replace("NAFDAC", " "))
    digits = re.sub(r"\D", "", text)
    if m and not (digits and len(digits) in (8, 12, 13, 14)
                  and len(digits) >= len(re.sub(r"\D", "", m.group(1)))):
        return _nafdac_reply(m.group(1))
    if digits and len(digits) in (8, 12, 13, 14):
        return _barcode_reply(digits)
    return "Vouch: send NAFDAC <number> or V <barcode>."


def _nafdac_reply(number: str) -> str:
    res = R.lookup(number)
    name = (res.matched.product_name if res.matched else "") or "This number"
    if res.verdict == R.REGISTERED:
        return (f"Vouch: {name} is registered"
                f"{' to ' + res.matched.manufacturer if res.matched else ''}."
                " A record match is not proof of this pack.")
    if res.verdict == R.MISMATCH:
        return (f"Vouch: {number} belongs to another product."
                " Do not use. Keep the pack and receipt.")
    if res.verdict == R.INACTIVE:
        return (f"Vouch: {number} is registered but inactive."
                " Ask the seller to confirm it.")
    if res.verdict == R.NOT_FOUND:
        return (f"Vouch: no record for {number}."
                " Do not use. Keep the pack and receipt.")
    return (f"Vouch: {number} could not be confirmed."
            " Check the digits and retry.")


def _barcode_reply(code: str) -> str:
    if not B.checksum_ok(code):
        return f"Vouch: {code} fails its check digit. Re-read the barcode."
    try:
        res = B.lookup(code)
    except Exception:
        res = None
    item = res.item if res else None
    if item is None:
        return (f"Vouch: no product record for {code}."
                " That alone proves nothing.")
    who = item.name or "A product record"
    return (f"Vouch: {code} is listed as {who}."
            " A listing is not proof of this pack.")
