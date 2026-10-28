"""
Vouch — the check engine.

Takes what a person (or the vision layer) can read off a pack, combines it with
the NAFDAC registry, and produces a set of independent checks, a risk band, and
the actions that follow.

Design commitments:
  * No single "authenticity score". A score invites a verdict nobody can defend.
  * Every check states what it proves, what it does not, and its source.
  * Absence of evidence never becomes evidence of falsification.
  * The output is a work order for the next actor, not a judgement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from typing import Any

import expiry as E
import registry as R
import barcode as B

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

VERIFIED = "verified"
ATTENTION = "attention"
HIGH = "high_risk"
UNKNOWN = "unverifiable"
NA = "not_applicable"

BAND_CLEAR = "clear"
BAND_REVIEW = "review"
BAND_HIGH = "high_risk"
BAND_INCOMPLETE = "incomplete"

DISCLOSURE = (
    "Vouch is evidence assistance, not a regulator. It reports what it could "
    "check and what it could not. It does not certify a product as safe, and it "
    "does not declare a product fake. A 'high-risk' result means: preserve the "
    "pack, keep the receipt, and let NAFDAC decide."
)


@dataclass
class Check:
    id: str
    label: str
    state: str
    severity: float
    evidence: str
    proves: str
    does_not_prove: str
    source: str
    detail: list[str] = field(default_factory=list)

    @property
    def participates(self) -> bool:
        return self.state in (VERIFIED, ATTENTION, HIGH)


@dataclass
class Action:
    kind: str
    label: str
    why: str
    urgency: int           # 0 now, 1 today, 2 this week
    channel: str = ""


@dataclass
class PackInput:
    nafdac_number: str = ""
    barcode: str = ""
    manufacturer: str = ""
    product_name: str = ""
    batch: str = ""
    mfg_date: str = ""
    exp_date: str = ""
    pack_size: str = ""
    strength: str = ""
    flags: list[str] = field(default_factory=list)
    seller: str = ""
    location: str = ""
    price: str = ""
    purchased_on: str = ""
    image_path: str = ""
    extraction_confidence: float = 0.0


@dataclass
class Assessment:
    band: str
    headline: str
    checks: list[Check]
    actions: list[Action]
    score: float
    registry: dict | None
    input: dict
    disclosure: str = DISCLOSURE
    generated_at: str = ""
    kind: str = "medicine"
    barcode: dict | None = None
    rail: str = "nafdac"

    def to_dict(self) -> dict:
        return {
            "band": self.band,
            "headline": self.headline,
            "score": round(self.score, 2),
            "checks": [asdict(c) for c in self.checks],
            "actions": [asdict(a) for a in self.actions],
            "registry": self.registry,
            "barcode": self.barcode,
            "kind": self.kind,
            "rail": self.rail,
            "input": self.input,
            "disclosure": self.disclosure,
            "generated_at": self.generated_at,
            "counts": self.counts(),
        }

    def counts(self) -> dict:
        c = {VERIFIED: 0, ATTENTION: 0, HIGH: 0, UNKNOWN: 0, NA: 0}
        for chk in self.checks:
            c[chk.state] = c.get(chk.state, 0) + 1
        return c


# --------------------------------------------------------------------------
# Batch plausibility
# --------------------------------------------------------------------------

_BATCH_SHAPES = [
    (r"^[A-Z]{0,3}\d{3,8}$", "letters+digits"),
    (r"^[A-Z0-9]{4,12}$", "alphanumeric"),
]


def batch_check(batch: str, seen_batches: list[str] | None = None) -> Check:
    """Is this a plausible batch/lot number, and does it fit known patterns?"""
    b = (batch or "").strip()
    if not b:
        return Check(
            id="batch", label="Batch number", state=UNKNOWN, severity=0,
            evidence="No batch number was read.",
            proves="Nothing.",
            does_not_prove="That the batch is absent; it may be printed where "
                           "the camera could not reach.",
            source="pack",
            detail=["Photograph the crimp, the base of the carton, and the foil."],
        )

    signals: list[str] = []
    if len(b) < 3:
        signals.append("Batch number is implausibly short.")
    if len(b) > 20:
        signals.append("Batch number is implausibly long.")
    if not re.search(r"\d", b):
        signals.append("Batch number contains no digits, which is unusual.")

    norm = re.sub(r"[^A-Z0-9]", "", b.upper())
    if len(set(norm)) == 1 and len(norm) > 2:
        signals.append("Batch number is a single repeated character.")
    if norm in {"000000", "0000", "123456", "12345", "ABCDEF", "TEST", "SAMPLE"}:
        signals.append(f"Batch number '{b}' is a known placeholder value.")

    # A placeholder is a stronger signal than an unusual shape: a manufacturer
    # may print any shape it likes, but nobody prints "0000" on real stock.
    # Weighting them the same would let the weakest observation decide the band.
    severe = bool(signals) and (
        len(b) < 3 or norm in {"000000", "0000", "123456", "12345",
                               "ABCDEF", "TEST", "SAMPLE"}
        or (len(set(norm)) == 1 and len(norm) > 2)
    )

    # Compare against the batch shapes already reported for this product.
    shape_note = ""
    if seen_batches:
        def shape(x: str) -> str:
            n = re.sub(r"[^A-Z0-9]", "", x.upper())
            return re.sub(r"\d", "9", re.sub(r"[A-Z]", "A", n))
        mine = shape(b)
        peers = [shape(x) for x in seen_batches if x and x != b]
        if peers and mine not in peers:
            shape_note = (f"This batch has shape {mine}; "
                          f"{len(peers)} other reports use {', '.join(sorted(set(peers))[:3])}.")

    if signals or shape_note:
        return Check(
            id="batch", label="Batch number", state=ATTENTION,
            severity=1.5 if severe else 1.0,
            evidence=b,
            proves="The batch string looks irregular or unlike this product's peers.",
            does_not_prove="That the batch does not exist. Only a manufacturer "
                           "or NAFDAC can confirm a specific lot.",
            source="pack + Vouch reports",
            detail=signals + ([shape_note] if shape_note else []),
        )

    return Check(
        id="batch", label="Batch number", state=VERIFIED, severity=0,
        evidence=b,
        proves="The batch string is well formed.",
        does_not_prove="That this lot was made by the registered manufacturer.",
        source="pack",
    )


# --------------------------------------------------------------------------
# Integrity flags (from the person or the vision layer)
# --------------------------------------------------------------------------

FLAG_LABELS = {
    "no_expiry": "No expiry date visible",
    "expiry_overprint": "Expiry looks printed over something else",
    "sticker_over_print": "A sticker covers printed information",
    "seal_broken": "Seal or tamper band is broken",
    "hologram_missing": "Expected hologram is missing",
    "misspelling": "Spelling or logo error on the pack",
    "poor_print": "Print quality is visibly poor or blurred",
    "different_batch_print": "Batch and expiry printed in different styles",
    "loose_tablets": "Tablets or capsules loose or damaged in the pack",
    "no_leaflet": "No patient leaflet in a carton that should have one",
}

FLAG_WEIGHT = {
    "expiry_overprint": 2.0,
    "sticker_over_print": 2.0,
    "different_batch_print": 1.5,
    "misspelling": 2.0,
    "hologram_missing": 1.5,
    "seal_broken": 1.5,
    "poor_print": 1.0,
    "no_expiry": 1.5,
    "loose_tablets": 1.0,
    "no_leaflet": 1.0,
}


def integrity_check(flags: list[str]) -> Check:
    picked = [f for f in flags if f in FLAG_LABELS]
    if not picked:
        return Check(
            id="integrity", label="Pack integrity", state=NA, severity=0,
            evidence="No integrity problem was reported.",
            proves="Nothing by itself.",
            does_not_prove="That the pack is intact — nobody inspected it.",
            source="person",
            detail=["Tick what you can see; the photos matter more than the ticks."],
        )
    severity = min(3.0, sum(FLAG_WEIGHT.get(f, 1.0) for f in picked))
    state = HIGH if severity >= 2.5 else ATTENTION
    return Check(
        id="integrity", label="Pack integrity", state=state, severity=severity,
        evidence="; ".join(FLAG_LABELS[f] for f in picked),
        proves="Someone observed physical inconsistencies on this pack.",
        does_not_prove="Who caused them, or when. Poor printing also happens on "
                       "properly licensed stock that was stored badly.",
        source="person",
        detail=[FLAG_LABELS[f] for f in picked],
    )


# --------------------------------------------------------------------------
# Kinds and the barcode rail
# --------------------------------------------------------------------------

_REGISTRY_KIND = {
    "Drugs": "medicine",
    "Vaccines and Biologics": "vaccine",
    "Herbals and Nutraceuticals": "herbal",
    "Medical devices": "device",
    "Veterinary": "veterinary",
    "Disinfectants": "disinfectant",
}


def _kind_of(pack: PackInput, reg: R.Result | None, bar: B.Result | None) -> str:
    """What kind of thing is this? It only chooses vocabulary — never a verdict."""
    if reg is not None and reg.matched is not None:
        got = _REGISTRY_KIND.get(getattr(reg.matched, "category", "") or "")
        if got:
            return got
    if bar is not None and bar.item is not None:
        return B.kind_of(bar.item.categories, bar.item.name, bar.item.source)
    if (pack.barcode or "").strip():
        return "product"
    text = f"{pack.product_name} {pack.manufacturer}".lower()
    if re.search(r"\b(water|sachet water)\b", text):
        return "water"
    if re.search(r"\b(drink|juice|malt|cola|soda|milk|beverage|tea|coffee)\b", text):
        return "beverage"
    if re.search(r"\b(cream|lotion|soap|shampoo|cosmetic|deodorant)\b", text):
        return "cosmetic"
    if re.search(r"\b(device|syringe|condom|diaper|thermometer)\b", text):
        return "device"
    return "medicine"


def _barcode_checks(pack: PackInput, bar: B.Result, kind: str) -> list[Check]:
    """Everything a barcode can honestly tell you, and nothing more."""
    code = bar.code
    out: list[Check] = []

    if bar.unavailable:
        out.append(Check(
            id="barcode", label="Barcode record", state=UNKNOWN, severity=0,
            evidence=bar.message or "The product databases could not be reached.",
            proves="Nothing.",
            does_not_prove="Anything about this pack. An unreachable database is "
                           "silence, not a finding.",
            source="openfoodfacts.org / openbeautyfacts.org",
            detail=["Retry when the connection is back."],
        ))
        return out

    if not bar.found:
        evidence = f"Barcode {code} is not in any consumer product database we can query."
        if bar.valid is False:
            evidence = (f"Barcode {code} fails its own check digit, so the "
                        f"number as printed cannot be a valid EAN/UPC.")
        out.append(Check(
            id="barcode", label="Barcode record", state=ATTENTION, severity=1.5,
            evidence=evidence,
            proves=("The code on this pack cannot be a correctly printed barcode."
                    if bar.valid is False else
                    "No product database has a record of this code."),
            does_not_prove="That the product is falsified. A mistyped digit, a "
                           "new product, a shop repack and a local brand all "
                           "look exactly like this.",
            source="openfoodfacts.org / openbeautyfacts.org",
            detail=(bar.signals or []) + [
                "Re-read the digits by eye before treating this as a signal."],
        ))
        return out

    item = bar.item
    who = " ".join(x for x in (item.brand, item.name) if x).strip() or code
    idm = B.identity(pack.product_name, pack.manufacturer, item)

    if idm["verdict"] == "unknown":
        out.append(Check(
            id="barcode", label="Barcode identity", state=UNKNOWN, severity=0,
            evidence=(f"Barcode {code} resolves to '{item.name}'"
                      f"{' by ' + item.brand if item.brand else ''} in "
                      f"{item.source}. No product name from the pack was "
                      f"entered, so there is nothing to compare it with yet."),
            proves="The code is a real product record.",
            does_not_prove="That the product in your hand is that product. "
                           "Add the name printed on the pack to compare.",
            source=item.source,
            detail=["Re-scan the label, or type the product name, then check again."],
        ))
    elif idm["verdict"] == "different":
        out.append(Check(
            id="barcode", label="Barcode identity", state=HIGH, severity=3.0,
            evidence=(f"Barcode {code} resolves to '{item.name}'"
                      f"{' by ' + item.brand if item.brand else ''} in "
                      f"{item.source}. The pack says '{pack.product_name}'"
                      f"{' by ' + pack.manufacturer if pack.manufacturer else ''}."),
            proves="The code printed on this pack belongs to a different product.",
            does_not_prove="That the contents are harmful. Product databases are "
                           "crowd-sourced and can lag a genuine repackaging; "
                           "confirm the code against a second pack.",
            source=f"{item.source} + pack",
            detail=[f"Name similarity {idm['name_score']:.2f}, brand "
                    f"similarity {idm['brand_score']:.2f}."],
        ))
    elif idm["verdict"] == "partial":
        out.append(Check(
            id="barcode", label="Barcode identity", state=ATTENTION,
            severity=1.5,
            evidence=(f"Barcode {code} resolves to '{who}', which only partly "
                      f"matches '{pack.product_name}'."),
            proves="The match between the pack and the database record is loose.",
            does_not_prove="That the pack is wrong. Database names are entered "
                           "by volunteers and often drop the descriptor.",
            source=f"{item.source} + pack",
        ))
    else:
        out.append(Check(
            id="barcode", label="Barcode identity", state=VERIFIED, severity=0,
            evidence=(f"Barcode {code} — {item.name}"
                      f"{' — ' + item.brand if item.brand else ''}"
                      f" ({item.source}, {item.countries or 'origin not listed'})."),
            proves="The code on the pack resolves to this product.",
            does_not_prove="That the pack is authentic or the contents correct. "
                           "A copied barcode prints as easily as a copied number.",
            source=item.source,
            detail=[f"Name similarity {idm['name_score']:.2f}, brand similarity "
                    f"{idm['brand_score']:.2f}."],
        ))

    return out


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

def assess(
    pack: PackInput,
    today: date | None = None,
    offline: bool = False,
    seen_batches: list[str] | None = None,
    registry_result: R.Result | None = None,
    barcode_result: B.Result | None = None,
) -> Assessment:
    today = today or date.today()
    checks: list[Check] = []
    reg = registry_result
    has_number = bool((pack.nafdac_number or "").strip())
    has_barcode = bool((pack.barcode or "").strip())

    if reg is None and has_number:
        try:
            reg = R.lookup(
                pack.nafdac_number,
                manufacturer_on_pack=pack.manufacturer or None,
                product_name_on_pack=pack.product_name or None,
                offline=offline,
            )
        except R.RegistryUnavailable:
            reg = None

    bar = barcode_result
    if bar is None and has_barcode:
        try:
            bar = B.lookup(pack.barcode, offline=offline)
        except B.BarcodeUnavailable:
            bar = B.Result(pack.barcode, None, unavailable=True,
                           message="The product databases could not be reached.")

    kind = _kind_of(pack, reg, bar)
    expiry_label = ("Best before"
                    if kind in ("food", "beverage", "water") else "Expiry date")

    # 1. Barcode: the identity rail for food, drink and everyday consumables.
    if has_barcode and bar is not None:
        checks.extend(_barcode_checks(pack, bar, kind))

    # 2. Registry: does the number exist, and to whom?
    if not has_number:
        if has_barcode:
            checks.append(Check(
                id="registry", label="NAFDAC registration", state=UNKNOWN,
                severity=0,
                evidence="Not checkable here: the public register we can query "
                         "lists medicines, devices, vaccines, herbals and "
                         "veterinary products — not food, drink or cosmetics.",
                proves="Nothing about this pack.",
                does_not_prove="That it is unregistered. NAFDAC regulates food "
                               "and cosmetics, but does not publish those "
                               "registrations in the open register.",
                source="greenbook.nafdac.gov.ng",
                detail=["A NAFDAC number printed on the pack is still recorded "
                        "in any report you file from here.",
                        "This is a gap in what is published, not a finding "
                        "against this product."],
            ))
        else:
            checks.append(Check(
                id="registry", label="NAFDAC registration", state=ATTENTION,
                severity=1.5,
                evidence="No NAFDAC number was read from this pack.",
                proves="Registration could not be checked at all.",
                does_not_prove="That the product is unregistered. The number may "
                               "be printed where the camera could not reach.",
                source="pack",
                detail=["Every medicine sold in Nigeria must carry a NAFDAC "
                        "registration number. Find it on the back or side panel.",
                        "If there is genuinely no number anywhere on the pack, "
                        "that is itself a finding."],
            ))
    elif reg is None:
        checks.append(Check(
            id="registry", label="NAFDAC registration", state=UNKNOWN, severity=0,
            evidence="The registry could not be reached.",
            proves="Nothing.",
            does_not_prove="Anything about this pack.",
            source="greenbook.nafdac.gov.ng",
            detail=["Retry when the connection is back; do not treat this as a signal."],
        ))
    elif reg.verdict == R.REGISTERED:
        m = reg.matched
        checks.append(Check(
            id="registry", label="NAFDAC registration", state=VERIFIED, severity=0,
            evidence=f"{m.nafdac} — {m.product_name} — {m.manufacturer} ({m.status})",
            proves="This number is registered and active, to this company.",
            does_not_prove="That this physical pack was made by that company.",
            source="greenbook.nafdac.gov.ng",
        ))
    elif reg.verdict == R.MISMATCH:
        m = reg.matched
        checks.append(Check(
            id="registry", label="NAFDAC registration", state=HIGH, severity=3.0,
            evidence=f"Number belongs to {m.manufacturer} ({m.nafdac}, "
                     f"{m.product_name}); the pack names {pack.manufacturer or 'another company'}.",
            proves="The number on this pack is registered to a different company.",
            does_not_prove="That the medicine inside is wrong. It proves the "
                           "packaging is lying about who made it.",
            source="greenbook.nafdac.gov.ng + pack",
            detail=reg.signals,
        ))
    elif reg.verdict == R.INACTIVE:
        m = reg.matched
        checks.append(Check(
            id="registry", label="NAFDAC registration", state=ATTENTION, severity=2.0,
            evidence=f"{m.nafdac} — {m.product_name} — status {m.status}, "
                     f"registration expiry {m.registration_expiry}.",
            proves="This registration is not currently active.",
            does_not_prove="That the pack is falsified. Stock lawfully "
                           "manufactured before a registration lapsed can "
                           "still be in circulation.",
            source="greenbook.nafdac.gov.ng",
        ))
    elif reg.verdict == R.NOT_FOUND:
        checks.append(Check(
            id="registry", label="NAFDAC registration", state=HIGH, severity=3.0,
            evidence="No registry record for this number in any known format.",
            proves="The printed number does not match any registered product.",
            does_not_prove="That the product is falsified. A misread digit "
                           "produces the same result; confirm the number by eye "
                           "first.",
            source="greenbook.nafdac.gov.ng",
            detail=reg.signals,
        ))
    else:  # AMBIGUOUS / UNVERIFIABLE
        checks.append(Check(
            id="registry", label="NAFDAC registration", state=UNKNOWN, severity=0,
            evidence=reg.message,
            proves="The lookup was inconclusive.",
            does_not_prove="Anything either way.",
            source="greenbook.nafdac.gov.ng",
            detail=reg.signals,
        ))

    # 2. Manufacturer identity is already folded into the registry check above
    #    when the registry is authoritative. Add a separate check only when the
    #    registry itself was unreadable, so we do not double-count.

    # 3. Product identity vs the registered name.
    if reg is not None and reg.matched and pack.product_name:
        s = R.similarity(pack.product_name, reg.matched.product_name)
        if s < 0.55:
            checks.append(Check(
                id="product_identity", label="Product identity", state=ATTENTION,
                severity=1.5,
                evidence=f"Pack says '{pack.product_name}'; registry says "
                         f"'{reg.matched.product_name}' (similarity {s:.2f}).",
                proves="The pack name and the registered name diverge.",
                does_not_prove="Falsification — brands restyle names, and packs "
                               "print marketing names the registry does not use.",
                source="greenbook.nafdac.gov.ng + pack",
            ))
        else:
            # A clean result must be earned, not merely assumed. If the name
            # reconciles we say so, with the registry's own wording beside it.
            checks.append(Check(
                id="product_identity", label="Product identity", state=VERIFIED,
                severity=0,
                evidence=f"Pack '{pack.product_name}' matches registry "
                         f"'{reg.matched.product_name}' (similarity {s:.2f}).",
                proves="The name on the pack is the name on the registration.",
                does_not_prove="That this physical pack came from that factory. "
                               "Printing a matching name costs nothing.",
                source="greenbook.nafdac.gov.ng + pack",
            ))

    # 4. Pack expiry.
    exp_assess = E.assess_expiry(pack.exp_date, pack.mfg_date, today=today)
    if exp_assess.state == E.EXPIRED:
        checks.append(Check(
            id="expiry", label=expiry_label, state=HIGH, severity=3.0,
            evidence=exp_assess.message,
            proves="The stated expiry has passed.",
            does_not_prove="That the medicine inside is something else. Expired "
                           "is its own, provable finding and is enough to act on.",
            source="pack",
            detail=exp_assess.signals,
        ))
    elif exp_assess.state == E.EXPIRING:
        checks.append(Check(
            id="expiry", label=expiry_label, state=ATTENTION, severity=1.5,
            evidence=exp_assess.message,
            proves="The pack is close to expiry.",
            does_not_prove="That it is unsafe today.",
            source="pack", detail=exp_assess.signals,
        ))
    elif exp_assess.state in (E.IMPLAUSIBLE, E.SEQUENCE_ERROR):
        checks.append(Check(
            id="expiry", label=expiry_label, state=HIGH, severity=2.5,
            evidence=exp_assess.message,
            proves="The printed dates are internally impossible.",
            does_not_prove="Which date is wrong, or who printed it.",
            source="pack", detail=exp_assess.signals,
        ))
    elif exp_assess.state == E.UNREADABLE:
        checks.append(Check(
            id="expiry", label=expiry_label, state=ATTENTION, severity=1.5,
            evidence=exp_assess.message,
            proves="No expiry could be read.",
            does_not_prove="That it is absent; it may be in shadow or worn.",
            source="pack",
            detail=["A medicine without a legible expiry cannot be sold lawfully "
                    "in Nigeria."] + exp_assess.signals,
        ))
    else:
        checks.append(Check(
            id="expiry", label=expiry_label, state=VERIFIED, severity=0,
            evidence=exp_assess.message,
            proves="The pack is inside its stated shelf life.",
            does_not_prove="Correct storage. Heat and sunlight degrade medicine "
                           "well before the printed date.",
            source="pack", detail=exp_assess.signals,
        ))

    # 5. Registration window: does the pack claim shelf life beyond approval?
    if (
        reg is not None and reg.matched
        and reg.matched.registration_expiry
        and exp_assess.exp is not None
    ):
        try:
            reg_exp = datetime.strptime(reg.matched.registration_expiry, "%Y-%m-%d").date()
        except ValueError:
            reg_exp = None
        if reg_exp and exp_assess.exp.end_of_period() > reg_exp:
            gap = (exp_assess.exp.end_of_period() - reg_exp).days
            checks.append(Check(
                id="registration_window", label="Registration window",
                state=ATTENTION, severity=2.0,
                evidence=f"Pack expires {exp_assess.exp.end_of_period()}, "
                         f"registration lapsed {reg_exp} ({gap} days later).",
                proves="The pack claims shelf life beyond the approval window.",
                does_not_prove="Falsification. The manufacturer may have renewed "
                               "under a new number, or the registry may lag.",
                source="greenbook.nafdac.gov.ng + pack",
                detail=["This is the kind of gap no consumer lookup currently surfaces."],
            ))

    # 6. Batch plausibility.
    checks.append(batch_check(pack.batch, seen_batches))

    # 7. Pack integrity, as observed by the person or the vision layer.
    checks.append(integrity_check(pack.flags))

    # ---- Score and band -------------------------------------------------
    score = sum(c.severity for c in checks if c.state in (ATTENTION, HIGH))
    # INVARIANT: a check that says "high_risk" puts the whole pack in the
    # high-risk band. Anything else makes the state vocabulary a lie — the UI
    # would show a red check inside an amber banner. The score is only used to
    # aggregate *moderate* findings, where several weak signals can add up to
    # something worth acting on.
    hard = any(c.state == HIGH for c in checks)

    if hard or score >= 5.0:
        # A single hard finding, or so many independent moderate findings that
        # one of them is likely real. This outranks "incomplete": a red finding
        # must never sit inside a grey banner. We do not reach this band on one
        # soft signal: over-flagging is its own kind of failure.
        band = BAND_HIGH
    elif not (pack.nafdac_number or pack.exp_date):
        band = BAND_INCOMPLETE
    elif score >= 1.5:
        band = BAND_REVIEW
    else:
        band = BAND_CLEAR

    actions = _actions_for(band, checks, pack, reg, exp_assess, kind)
    headline = _headline(band, checks, score, kind)

    return Assessment(
        band=band,
        headline=headline,
        checks=checks,
        actions=actions,
        score=score,
        registry=asdict(reg.matched) if (reg and reg.matched) else (
            {"verdict": reg.verdict} if reg else None),
        input=asdict(pack),
        generated_at=datetime.now().isoformat(timespec="seconds"),
        kind=kind,
        barcode=(bar.item.to_dict() if (bar and bar.item) else
                 ({"code": bar.code, "found": False, "valid": bar.valid}
                  if bar else None)),
        rail="barcode" if has_barcode and not has_number else "nafdac",
    )


def _headline(band: str, checks: list[Check], score: float,
              kind: str = "medicine") -> str:
    high = [c for c in checks if c.state == HIGH]
    att = [c for c in checks if c.state == ATTENTION]
    if band == BAND_HIGH:
        if high:
            parts = [c.label for c in high][:3]
            return (f"{len(high)} high-risk signal{'s' if len(high) != 1 else ''} "
                    f"({', '.join(parts)}). Preserve the pack and keep the receipt.")
        # Several moderate findings that individually would not justify action.
        parts = [c.label for c in att][:3]
        return ("Several things wrong at once "
                f"({', '.join(parts)}), none decisive alone. Do not use this pack "
                "until they are checked.")
    if band == BAND_REVIEW:
        if not att:
            # Defensive: the band is derived from attention/high checks, so an
            # empty list should be impossible. Say something true if it happens.
            return "Something here is worth checking before you use this pack."
        names = [c.label for c in att][:3]
        return (f"{len(att)} thing{'s' if len(att) != 1 else ''} worth checking "
                f"before you use this ({'; '.join(names)}).")
    if band == BAND_INCOMPLETE:
        if kind in ("food", "beverage", "water", "cosmetic", "household"):
            return ("Not enough information yet. Add the expiry date and the "
                    "product name printed on the pack.")
        return "Not enough information yet. Add the NAFDAC number or the expiry date."
    return "Nothing wrong found in the checks that can be done from here."


def _actions_for(
    band: str,
    checks: list[Check],
    pack: PackInput,
    reg: R.Result | None,
    exp_assess: E.ExpiryAssessment,
    kind: str = "medicine",
) -> list[Action]:
    ids = {c.id for c in checks if c.state in (ATTENTION, HIGH)}
    out: list[Action] = []

    if band == BAND_INCOMPLETE:
        if kind in ("food", "beverage", "water", "cosmetic", "household"):
            out.append(Action("complete", "Photograph the date panel",
                              "The best-before date and product name are printed there.",
                              0, "camera"))
            out.append(Action("manual", "Type the barcode and expiry date",
                              "Faster than re-shooting if the print is clear.",
                              0, "ui"))
        else:
            out.append(Action("complete", "Photograph the back panel",
                              "The NAFDAC number and expiry are printed there.",
                              0, "camera"))
            out.append(Action("manual", "Type the NAFDAC number or barcode",
                              "Faster than re-shooting if the print is clear.",
                              0, "ui"))
        return out

    out.append(Action("preserve", "Keep the pack, receipt and this report",
                      "Every later step depends on the physical evidence.",
                      0, "you"))

    consumable = kind in ("food", "beverage", "water", "cosmetic", "household")
    if band in (BAND_HIGH, BAND_REVIEW) and consumable:
        out.append(Action("stop", "Set this pack aside until it is checked",
                          "Do not eat, drink or use it while the findings stand.",
                          0, "you"))

    if "barcode" in ids and band != BAND_CLEAR:
        out.append(Action("recheck", "Re-read the barcode by eye",
                          "One mistyped digit produces the same result as a "
                          "falsified code.", 0, "you"))

    if "registry" in ids and reg is not None and reg.verdict == R.NOT_FOUND:
        out.append(Action("recheck", "Re-read the number by eye",
                          "One misread digit produces the same result as an "
                          "unregistered product.", 0, "you"))
        out.append(Action("nafdac_report", "Report to NAFDAC",
                          "An unregistered number on a medicine is a reportable finding.",
                          0, "regulator"))
    if "registry" in ids and reg is not None and reg.verdict == R.MISMATCH:
        out.append(Action("nafdac_report", "Report to NAFDAC",
                          "The number belongs to another company. That is the "
                          "copied-number pattern.", 0, "regulator"))
    if "registry" in ids and reg is not None and reg.verdict == R.INACTIVE:
        out.append(Action("ask_pharmacist", "Ask the pharmacist to confirm the "
                          "registration is current",
                          "A lapsed registration is a reason to check, not a verdict.",
                          1, "pharmacist"))
    if "expiry" in ids and exp_assess.state == E.EXPIRED:
        out.append(Action("refund", "Demand a refund or replacement",
                          "Selling expired medicine is a breach on its own, "
                          "whatever else is true about the pack.", 0, "seller"))
        out.append(Action("nafdac_report", "Report the seller to NAFDAC",
                          "Expired stock on a shelf is a public health finding.",
                          1, "regulator"))
    if "integrity" in ids:
        out.append(Action("photograph", "Photograph the defect in daylight",
                          "The overprint, sticker or seal is the evidence that "
                          "matters later.", 0, "camera"))
    if "batch" in ids:
        out.append(Action("batch_watch", "Watch this batch",
                          "If more people report the same lot, it becomes a "
                          "recall signal rather than an anecdote.", 2, "network"))

    if band in (BAND_HIGH, BAND_REVIEW):
        out.append(Action("refund", "Ask the seller for a refund",
                          "You do not need to prove misrepresentation to refuse "
                          "an expired or misdescribed product.", 1, "seller"))
        out.append(Action("open_case", "Open a case so this is chased",
                          "A finding with no follow-up is just a screenshot.",
                          1, "vouch"))

    if band == BAND_CLEAR:
        out.append(Action("keep_receipt", "Keep the receipt",
                          "The checks you can do from a phone all passed. That is "
                          "not a guarantee.", 2, "you"))

    # De-duplicate by kind, keeping the most urgent reason.
    best: dict[str, Action] = {}
    for a in out:
        if a.kind not in best or a.urgency < best[a.kind].urgency:
            best[a.kind] = a
    return sorted(best.values(), key=lambda a: (a.urgency, _action_rank(a.kind)))


# Order in which actions should appear. The first thing a frightened person
# needs is not a form: it is to keep the evidence, then re-read the number.
ACTION_ORDER = [
    "preserve", "stop", "photograph", "recheck", "complete", "manual",
    "refund", "ask_pharmacist", "nafdac_report", "open_case",
    "batch_watch", "keep_receipt",
]


def _action_rank(kind: str) -> int:
    try:
        return ACTION_ORDER.index(kind)
    except ValueError:
        return len(ACTION_ORDER)
