"""
Vouch — the action layer.

A finding with no follow-up is a screenshot. This module turns an assessment
into the three documents that actually move a case:

  1. a regulator report (NAFDAC)
  2. a consumer-protection complaint (FCCPC)
  3. a refund demand to the seller

Plus the escalation schedule that a real complaint needs, because the reason
people give up is not the first message — it is the fourth.

Rules held throughout:
  * We describe what was observed. We do not assert falsification.
  * We never give medical advice.
  * We name the seller only in the documents the user sends to a regulator,
    never in public output.
  * Every document carries the "not a regulator" disclaimer.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, timedelta

from engine import Assessment, PackInput, DISCLOSURE


# --------------------------------------------------------------------------
# Shared blocks
# --------------------------------------------------------------------------

def _n(v: str | None, fallback: str = "not recorded") -> str:
    v = (v or "").strip()
    return v if v else fallback


def _money(price: str) -> str:
    p = (price or "").strip()
    if not p:
        return "the amount paid"
    if p.upper().startswith(("N", "₦")):
        return p
    return f"₦{p}"


def _check_lines(a: Assessment) -> str:
    out = []
    for c in a.checks:
        if c.state == "not_applicable":
            continue
        mark = {
            "verified": "checked, no problem found",
            "attention": "needs attention",
            "high_risk": "high-risk signal",
            "unverifiable": "could not be checked",
        }.get(c.state, c.state)
        out.append(f"  - {c.label}: {mark}")
        out.append(f"      observed: {c.evidence}")
        out.append(f"      what it does not prove: {c.does_not_prove}")
    return "\n".join(out)


def _registry_block(a: Assessment) -> str:
    r = a.registry or {}
    if not r or not r.get("nafdac"):
        return "  No registry record was attached to this report."
    return "\n".join([
        f"  Registered number : {r.get('nafdac')}",
        f"  Registered name   : {r.get('product_name')}",
        f"  Registration holder: {r.get('manufacturer')}",
        f"  Status            : {r.get('status')}",
        f"  Approved          : {r.get('approval_date')}",
        f"  Registration expiry: {r.get('registration_expiry')}",
        f"  Composition       : {_n(r.get('composition'))}",
        f"  Pack size         : {_n(r.get('pack_size'))}",
        "  Source            : NAFDAC Greenbook, greenbook.nafdac.gov.ng",
    ])


def _product_block(p: PackInput) -> str:
    return "\n".join([
        f"  Product name on pack : {_n(p.product_name)}",
        f"  NAFDAC number printed: {_n(p.nafdac_number)}",
        f"  Manufacturer on pack : {_n(p.manufacturer)}",
        f"  Batch / lot number   : {_n(p.batch)}",
        f"  Expiry printed       : {_n(p.exp_date)}",
        f"  Manufacture printed  : {_n(p.mfg_date)}",
        f"  Strength             : {_n(p.strength)}",
        f"  Pack size on pack    : {_n(p.pack_size)}",
        f"  Seller               : {_n(p.seller)}",
        f"  Location             : {_n(p.location)}",
        f"  Price paid           : {_n(p.price)}",
        f"  Date purchased       : {_n(p.purchased_on)}",
    ])


@dataclass
class Document:
    kind: str
    title: str
    filename: str
    body: str
    channel: str
    notes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# 1. Regulator report
# --------------------------------------------------------------------------

def nafdac_report(p: PackInput, a: Assessment) -> Document:
    signals = [c for c in a.checks if c.state in ("attention", "high_risk")]
    headline = ", ".join(c.label for c in signals) or "no specific signal"
    body = f"""PRODUCT CONCERN REPORT
Prepared with Vouch on {a.generated_at[:10]}

SUMMARY
  A medicine purchased at retail was checked against the NAFDAC Greenbook
  registry and inspected for expiry and batch irregularities. The following
  signals were recorded: {headline}.

  This report describes observations. It does not assert that the product is
  falsified. The determination is NAFDAC's to make.

1. PRODUCT AS PURCHASED
{_product_block(p)}

2. WHAT WAS OBSERVED
{_check_lines(a)}

3. REGISTRY RECORD FOR THE PRINTED NUMBER
{_registry_block(a)}

4. EVIDENCE HELD BY THE COMPLAINANT
  - Photographs of the front, back, batch and expiry printing
  - The physical pack, unopened where possible
  - The purchase receipt, if retained
  - This report

5. REQUESTED ACTION
  - Confirm whether the printed number is valid for this product
  - Confirm whether the lot number is known to the registration holder
  - Inspect the seller's stock if the number or dates cannot be reconciled

6. DECLARATION
  The information above is true to the best of my knowledge. I understand that
  this is a report of a concern, not a legal finding.

{DISCLOSURE}
"""
    return Document(
        kind="nafdac_report",
        title="NAFDAC product concern report",
        filename=f"nafdac-report-{_slug(p.nafdac_number or 'product')}.txt",
        body=body,
        channel="NAFDAC",
        notes=[
            "Submit through NAFDAC's official channels and keep the reference number.",
            "Confirm the current channel and office on nafdac.gov.ng before sending.",
            "NAFDAC care line: 0700-1-NAFDAC.",
            "Do not post the seller's name publicly; the regulator decides culpability.",
        ],
    )


# --------------------------------------------------------------------------
# 2. Consumer-protection complaint
# --------------------------------------------------------------------------

def fccpc_complaint(p: PackInput, a: Assessment) -> Document:
    expired = any(
        c.id == "expiry" and "Expired" in c.evidence for c in a.checks
    )
    subject_kind = "expired medicine" if expired else "misdescribed medicine"
    body = f"""COMPLAINT TO THE FEDERAL COMPETITION AND CONSUMER PROTECTION COMMISSION
Under the Federal Competition and Consumer Protection Act 2018

COMPLAINANT
  Name     : ______________________
  Phone    : ______________________
  Address  : ______________________
  Email    : ______________________

RESPONDENT
  Seller / pharmacy : {_n(p.seller)}
  Location          : {_n(p.location)}

SUBJECT
  Supply of {subject_kind} and refusal to refund.

FACTS
  1. On {_n(p.purchased_on, 'the date shown on the receipt')} I purchased the
     product described below from the respondent for {_money(p.price)}.

{_product_block(p)}

  2. On checking the product against the NAFDAC Greenbook registry and
     inspecting the pack, the following was recorded:

{_check_lines(a)}

  3. I returned to the respondent seeking a refund or replacement. [Describe
     what happened, and on what date.]

EVIDENCE
  - The product pack, retained unopened where possible
  - Photographs of the pack, batch and expiry
  - Receipt or proof of purchase
  - This complaint

RELIEF SOUGHT
  a. A full refund of {_money(p.price)}.
  b. A written confirmation that the product has been withdrawn from sale.
  c. Any investigation the Commission considers appropriate into the supply of
     this product at this location.

DECLARATION
  The facts stated above are true to the best of my knowledge.

  Signed: ______________________     Date: ____________

{DISCLOSURE}
"""
    return Document(
        kind="fccpc_complaint",
        title="FCCPC consumer complaint",
        filename=f"fccpc-complaint-{_slug(p.nafdac_number or 'product')}.txt",
        body=body,
        channel="FCCPC",
        notes=[
            "File only after the seller has refused a refund, or after 7 days of silence.",
            "Attach the photographs and the receipt.",
            "Keep the pack; the Commission may ask for it.",
        ],
    )


# --------------------------------------------------------------------------
# 3. Refund demand
# --------------------------------------------------------------------------

def seller_demand(p: PackInput, a: Assessment) -> Document:
    reasons = [c.label for c in a.checks if c.state in ("attention", "high_risk")]
    reason_text = ", ".join(reasons) if reasons else "the problems observed"
    body = f"""DEMAND FOR REFUND OR REPLACEMENT

To: {_n(p.seller, 'the seller')}
    {_n(p.location, '')}

Date: {date.today().isoformat()}

I purchased the following product from you on {_n(p.purchased_on, 'the date on the receipt')}
for {_money(p.price)}:

  Product    : {_n(p.product_name)}
  NAFDAC no. : {_n(p.nafdac_number)}
  Batch      : {_n(p.batch)}
  Expiry     : {_n(p.exp_date)}

On inspection I recorded the following: {reason_text}.

I am therefore requesting a refund of {_money(p.price)}, or a replacement with a
product that does not have these problems, within SEVEN (7) DAYS of this notice.

I am keeping the pack, the receipt and photographs of the printing. If we cannot
resolve this directly, I will refer the matter to the Federal Competition and
Consumer Protection Commission and to NAFDAC, and I will attach this notice.

I would prefer to resolve this with you directly.

Name : ______________________
Phone: ______________________

{DISCLOSURE}
"""
    return Document(
        kind="seller_demand",
        title="Refund demand to the seller",
        filename=f"refund-demand-{_slug(p.nafdac_number or 'product')}.txt",
        body=body,
        channel="seller",
        notes=[
            "Send this before filing with the FCCPC; it usually resolves the matter.",
            "Keep proof of sending (screenshot, SMS, or a signed copy).",
            "Stay factual. Do not accuse the seller of a crime in writing.",
        ],
    )


# --------------------------------------------------------------------------
# 4. Escalation schedule
# --------------------------------------------------------------------------

@dataclass
class Step:
    day: int
    due: str
    action: str
    why: str


def escalation_plan(a: Assessment, start: date | None = None) -> list[Step]:
    start = start or date.today()
    high = a.band == "high_risk"
    plan = [
        Step(0, (start).isoformat(),
             "Keep the pack, the receipt and the photos together.",
             "Every later step depends on the physical evidence."),
        Step(0, (start).isoformat(),
             "Send the refund demand to the seller.",
             "A written demand creates a dated record and often ends the matter."),
    ]
    if high:
        plan.append(Step(1, (start + timedelta(days=1)).isoformat(),
                         "Submit the NAFDAC report.",
                         "A regulator can inspect stock; you cannot."))
    plan += [
        Step(7, (start + timedelta(days=7)).isoformat(),
             "If there is no refund, file the FCCPC complaint.",
             "Seven days is a reasonable window and shows you tried."),
        Step(14, (start + timedelta(days=14)).isoformat(),
             "Chase the regulator reference by phone.",
             "Unchased reports stall. This is the step most people skip."),
        Step(21, (start + timedelta(days=21)).isoformat(),
             "Escalate to the FCCPC with the regulator reference.",
             "A reference number turns a complaint into a case."),
        Step(30, (start + timedelta(days=30)).isoformat(),
             "Record the outcome and close the case.",
             "An outcome, even a bad one, is data: it tells the next buyer."),
    ]
    return plan


def all_documents(p: PackInput, a: Assessment) -> dict[str, Document]:
    return {
        "seller_demand": seller_demand(p, a),
        "nafdac_report": nafdac_report(p, a),
        "fccpc_complaint": fccpc_complaint(p, a),
    }


def _slug(s: str) -> str:
    out = "".join(ch if ch.isalnum() else "-" for ch in (s or "").lower())
    return "-".join(filter(None, out.split("-"))) or "product"
