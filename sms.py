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
import gs1
import napams
import registry as R
import safety


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
        return "Vouch: send NAFDAC <number>, V <barcode> or G <pack code>."


def _answer(text: str) -> str:
    stripped = text.strip()
    if stripped[:2].upper() == "G " or gs1.is_gs1(stripped):
        payload = stripped[2:].strip() if stripped[:2].upper() == "G " \
            else stripped
        return _gs1_reply(payload)
    m = re.search(r"([A-Z]{0,2}\s?\d{1,2}\s?-?\s?\d{3,7})",
                  text.upper().replace("NAFDAC", " "))
    digits = re.sub(r"\D", "", text)
    if m and not (digits and len(digits) in (8, 12, 13, 14)
                  and len(digits) >= len(re.sub(r"\D", "", m.group(1)))):
        return _nafdac_reply(m.group(1).strip())
    if digits and len(digits) in (8, 12, 13, 14):
        return _barcode_reply(digits)
    return "Vouch: send NAFDAC <number>, V <barcode> or G <pack code>."


def _alert_line(number: str) -> str:
    """A NAFDAC warning naming this number, or ''. Exact matches only."""
    try:
        hits = safety.match_alerts(nafdac=number)
    except Exception:
        return ""
    if not hits:
        return ""
    first = hits[0]
    ref = first.get("id") or "notice"
    when = f" ({first['published']})" if first.get("published") else ""
    return f" Also: NAFDAC alert {ref}{when} names this number."


def _gs1_reply(payload: str) -> str:
    return "Vouch: " + gs1.summarize(gs1.parse(payload))


def _nafdac_reply(number: str) -> str:
    res = R.lookup(number)
    name = (res.matched.product_name if res.matched else "") or "This number"
    extra = _alert_line(number)
    if res.verdict == R.REGISTERED:
        return (f"Vouch: {name} is registered"
                f"{' to ' + res.matched.manufacturer if res.matched else ''}."
                " A record match is not proof of this pack." + extra)
    if res.verdict == R.MISMATCH:
        return (f"Vouch: {number} belongs to another product."
                " Do not use. Keep the pack and receipt." + extra)
    if res.verdict == R.INACTIVE:
        return (f"Vouch: {number} is registered but inactive."
                " Ask the seller to confirm it." + extra)
    if res.verdict == R.NOT_FOUND:
        hit = napams.known(number)
        if hit and hit.get("source") == "list":
            return (f"Vouch: NAFDAC's {hit.get('list', 'list')} names "
                    f"{number} as {hit['product']}. A record match is "
                    f"not proof of this pack.")
        if hit:
            return (f"Vouch: NAPAMS lists {number} as {hit['product']}"
                    + (f" ({hit['maker']})" if hit.get("maker") else "")
                    + f", shared {hit['first_seen']}. A record match is "
                      f"not proof of this pack.")
        return (f"Vouch: no record for {number} in the medicines register."
                " Do not use. Keep the pack and receipt."
                " Food, water or cosmetics? Verify at"
                " registration.nafdac.gov.ng (NAPAMS)." + extra)
    return (f"Vouch: {number} could not be confirmed."
            " Check the digits and retry." + extra)


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
