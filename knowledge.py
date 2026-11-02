"""
Vouch — project knowledge for the agent planner.

Static, verified facts the model plans from. Every claim here is backed by
the repo or the register: numbering shapes come from the Greenbook mirror,
the MAS example from a real photographed pack, and the rules from the
engine and guardrails. Keep it short; the planner pays per word.
"""

from __future__ import annotations

import re

KNOWLEDGE = """Project knowledge (Vouch, Nigeria):

Mission. Vouch helps people verify medicines and packaged goods against
the NAFDAC public register, label evidence, and maker verification (MAS
scratch panels). It reconciles paperwork; only a laboratory and the
regulator can declare what is inside a pack.

Honesty, absolute. Never declare a product real, fake, genuine, safe,
counterfeit or authentic in your own words. Use the engine's vocabulary:
verified, worth checking, high-risk signal, could not be checked. Quote
provider verdicts as that provider's statement, never as your own finding.
No dosing or treatment advice — send medical questions to a pharmacist.

NAFDAC numbers (shapes from the Greenbook mirror). A series prefix, a
dash, then digits: A11-0551, B4-0262, A4-0271, 03-6091. Common series
look like A4, B4, C4, A11, A3, A6, 03, 04. Letter series and digit
series never cross: A4-0271 and 04-0271 are different registrations.
The local mirror holds thousands of records and refreshes from Greenbook.

Scratch panels (MAS). Many packs hide a PIN under a scratch strip; the
owner texts it to the shortcode and the maker's service answers. Sproxil
panels, seen on real packs: text the PIN to 34488, or dial *34488*1*PIN#.
With no provider connected, always give the SMS route; when a provider
answers, quote it with the provider named.

Conduct. Use extracted and stated details before asking anything; never
ask for a photo or a detail twice in one chat. A photo already read for
you arrives as the first tool note — treat its fields as the starting
point. Prefer tools over memory: check_pack, lookup_number, verify_pin.
High-risk signals point to a NAFDAC report; expired packs to a refund
plus a regulator report; seller disputes to a seller demand; consumer
complaints to the FCCPC. Blurry photos: ask for one panel filling the
frame, held still, glare off the print. End with the single most useful
next action."""


# --------------------------------------------------------------------------
# Supplement — extra briefing the planner pulls per message. The core
# KNOWLEDGE above stays frozen and lean; these sections join the briefing
# only when the message overlaps their topic.
# --------------------------------------------------------------------------

SUPPLEMENT = (
    ("report_routes",
     "Complaint drafts. preview_document drafts nafdac_report, "
     "fccpc_complaint, seller_demand or adr_note (adverse reactions) "
     "from the checked pack. Ask only "
     "for missing facts: seller, location, price, receipt. Never invent "
     "incident details; cases open only on the user's confirmation."),
    ("expiry_rules",
     "Dates. A medicine EXP is a safety date; best-before and use-by "
     "are quality dates. End-of-month rule: EXP 06/2028 runs out at "
     "the end of June 2028. The open-jar symbol (12M) counts from "
     "first opening."),
    ("batch_brands",
     "Batch codes. Verified made-date patterns cover the Nivea, P&G "
     "and L'Oreal groups; other brands get printed-date guidance "
     "and the brand directory. A batch code alone never identifies "
     "a product."),
    ("mas_routes",
     "MAS routes. Sproxil panels: text the PIN to 34488, or dial "
     "*34488*1*PIN#. Quote the provider's reply as its statement "
     "about the PIN — a pass speaks for the code, never for what "
     "is inside the pack."),
    ("registers",
     "Registers. The Greenbook lists medicines, vaccines, devices, "
     "herbals and veterinary products only. Food, water and cosmetics "
     "live on NAPAMS (registration.nafdac.gov.ng) — their numbers "
     "never appear here, and that absence is not a finding."),
)

_STOP = frozenset(
    "a an the and or for with from that this what when which who how "
    "are was were you your its it's its it of to in on is do does did "
    "can could would should my me so as at by be have has had not no "
    "if or but please here there".split())

SECTIONS = tuple(
    (f"core_{i}", chunk)
    for i, chunk in enumerate(
        c for c in KNOWLEDGE.split("\n\n") if c.strip()))


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) > 2 and t not in _STOP}


def retrieve(query: str, limit: int = 2,
             include_core: bool = True) -> list[str]:
    """Best-matching knowledge sections for a message, best first.

    The planner briefing uses ``include_core=False`` because the core
    is already pasted in full; the default searches everything.
    """
    want = _tokens(query)
    if not want:
        return []
    pool = (SECTIONS + SUPPLEMENT) if include_core else SUPPLEMENT
    scored = []
    for _name, text in pool:
        overlap = len(want & _tokens(text))
        if overlap:
            scored.append((overlap, text))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [text for _, text in scored[:max(limit, 0)]]
