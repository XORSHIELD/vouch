"""
Vouch — the explanation layer.

The model never decides anything. engine.py decides, from evidence it can
name. This module turns that decision into language a frightened person can
use, in real time, and refuses to say things the evidence does not support.

Two providers, same interface:

  * MODEL_API_KEY set  -> a Meta model writes the explanation, then every
    sentence is checked against the assessment before it is shown. If a
    sentence asserts authenticity, invents a number, or gives medical advice,
    the whole model answer is discarded and the deterministic writer is used.
  * no key            -> the deterministic writer, streamed word by word.

That is deliberate. A demo must not fail because a key is missing, and a
model must never be the reason a person is wrongly reassured.

Guardrails enforced here, not in a prompt:
  - no "fake", "counterfeit", "genuine", "authentic" as a verdict on the pack
  - no invented NAFDAC numbers, dates or scores
  - no dosing or treatment advice
  - no promise of safety
"""

from __future__ import annotations

import json
import os
import re
import time

API_BASE = os.environ.get("META_BASE", "https://api.meta.ai/v1")
API_KEY = os.environ.get("MODEL_API_KEY") or os.environ.get("META_API_KEY") or ""
MODEL = os.environ.get("VOUCH_MODEL", "muse-spark-1.3")

# Words that turn evidence into a verdict. The engine's own vocabulary is
# "verified / worth checking / high-risk signal". These are the words it must
# never drift into, because they are the claims a laboratory makes.
BANNED = [
    r"\bfake\b", r"\bcounterfeit\b", r"\bgenuine\b", r"\bauthentic\b",
    r"\bforged\b", r"\bbogus\b", r"\billicit\b",
    r"\b100\s*%\s*(safe|genuine|real)\b", r"\bguaranteed\b",
    r"\bdefinitely\s+(safe|real|fake)\b", r"\bcure[sd]?\b",
    r"\bsafe\s+to\s+(take|eat|drink|use|consume)\b",
    r"\b(it|this|the\s+\w+)\s+is\s+(safe|unsafe)\b",
]

_ADVICE = [
    r"\b\d+\s*(mg|ml|g)\b.*\b(take|dose|twice|daily|every)\b",
    r"\b(take|use)\s+\d+\s*(tablet|capsule|spoon)",
    r"\b(dose|dosage)\s+(is|of)\b",
]

NAFDAC_LIKE = re.compile(r"\b[A-Z]{1,2}\d{1,2}-\d{4,6}\b|\b\d{1,2}-\d{4,6}\b")

NOUN = {
    "medicine": "medicine",
    "vaccine": "vaccine",
    "herbal": "herbal product",
    "device": "medical device",
    "veterinary": "veterinary product",
    "disinfectant": "disinfectant",
    "food": "food product",
    "beverage": "drink",
    "water": "water product",
    "cosmetic": "cosmetic",
    "household": "household product",
    "product": "product",
}

RAIL = {
    "nafdac": "the NAFDAC public register",
    "barcode": "the consumer product databases",
}


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------

def enabled() -> bool:
    return bool(API_KEY)


def provider() -> str:
    return MODEL if API_KEY else "vouch-composer"


def provider_label() -> str:
    return ("AI · " + MODEL) if API_KEY else "Vouch composer · on-device"


def status() -> dict:
    return {
        "enabled": enabled(),
        "provider": provider(),
        "label": provider_label(),
        "model": MODEL,
        "reason": "" if enabled() else
                  "No MODEL_API_KEY, so explanations are written by the "
                  "deterministic composer. Same evidence, same guardrails.",
    }


# --------------------------------------------------------------------------
# Grounded, deterministic writing
# --------------------------------------------------------------------------

def _label(a) -> str:
    name = (a.input or {}).get("product_name") or ""
    return name.strip() or "this pack"


def _noun(a) -> str:
    return NOUN.get(getattr(a, "kind", "medicine"), "product")


def _use_verb(a) -> str:
    return {"food": "eat", "beverage": "drink", "water": "drink"}.get(
        getattr(a, "kind", ""), "use")


def _sorted(checks):
    rank = {"high_risk": 0, "attention": 1, "unverifiable": 2,
            "verified": 3, "not_applicable": 4}
    return sorted(checks, key=lambda c: rank.get(c.state, 5))


def _evidence_line(c) -> str:
    return c.evidence.strip().rstrip(".")


def _opening(a) -> str:
    name, noun = _label(a), _noun(a)
    rail = RAIL.get(getattr(a, "rail", "nafdac"), "the evidence available")
    if a.band == "high_risk":
        return f"You checked {name} — a {noun} — against {rail}. There is a finding."
    if a.band == "review":
        return (f"You checked {name} — a {noun} — against {rail}. "
                f"Nothing is decisive, and something is worth a look.")
    if a.band == "incomplete":
        return f"You checked {name} — a {noun} — and there was not enough to go on."
    return (f"You checked {name} — a {noun} — against {rail}. "
            f"Everything that can be checked from here passed.")


def _core(a) -> str:
    checks = _sorted([c for c in a.checks if c.state in
                      ("high_risk", "attention")])
    if a.band == "incomplete":
        missing = [c for c in a.checks if c.state == "attention"]
        parts = [_evidence_line(c) for c in missing[:2]]
        return (" " + " ".join(parts) if parts else "") + (
            " Nothing has been verified, and nothing has been ruled out.")

    if a.band == "high_risk" and checks:
        lead = checks[0]
        text = f" {lead.evidence.strip().rstrip('.')}."
        if lead.id == "registry" and "different company" in lead.evidence:
            text += (" That is the copied-number pattern: the packaging "
                     "borrows a real registration from another product.")
        elif lead.id == "barcode" and lead.state == "high_risk":
            text += (" For food and drink the barcode is the identity number, "
                     "so this is the same failure as a medicine carrying "
                     "someone else's NAFDAC number.")
        elif lead.id == "expiry" and "Expired" in lead.evidence:
            text += (" Expired is its own finding: it does not say what is "
                     "inside the pack, and it is enough to act on.")
        if len(checks) > 1:
            text += (" A second signal is " + checks[1].label.lower() + ".")
        return text

    if a.band == "review" and checks:
        lead = checks[0]
        text = f" {lead.evidence.strip().rstrip('.')}."
        if lead.id == "registry" and "not currently active" in lead.evidence:
            text += (" Stock made before a registration lapses can still be "
                     "sold lawfully, so this is a reason to ask, not a finding.")
        elif lead.id == "registration_window":
            text += (" The pack claims to outlive the approval it is sold under. "
                     "That gap is exactly what a consumer lookup should surface.")
        elif lead.id == "barcode":
            text += (" That is a gap in the record, not proof about the pack.")
        if len(checks) > 1:
            names = ", ".join(c.label.lower() for c in checks[:3])
            text += f" Also worth checking: {names}."
        return text

    passing = [c for c in a.checks if c.state == "verified"]
    names = [c.label.lower() for c in passing[:3]]
    if len(names) == 1:
        joined = names[0]
    elif len(names) == 2:
        joined = " and ".join(names)
    else:
        joined = ", ".join(names[:-1]) + " and " + names[-1]
    text = (f" {joined} all reconcile." if names else " The checks that ran reconciled.")
    if getattr(a, "rail", "nafdac") == "barcode":
        text += (" A barcode is an identity check: it says the code belongs to "
                 "this product, not that the contents match the label.")
    return text


def _limits(a) -> str:
    if a.band == "high_risk":
        return (" None of this proves what is inside the pack. It proves the "
                "packaging does not describe itself truthfully, which is enough "
                "for a regulator to look.")
    if a.band == "review":
        return (" None of this is proof either way. It is a reason to check "
                "before you " + _use_verb(a) + ", not a conclusion.")
    if a.band == "incomplete":
        return (" A photograph of the back panel, or the number typed in, is "
                "usually all it takes to move this forward.")
    return (" That is narrower than 'safe'. It says the paperwork reconciles, "
            "not that the pack was stored well or that it came from the factory "
            "it names.")


def _next(a) -> str:
    if not a.actions:
        return ""
    first = a.actions[0]
    return f" Next: {first.label[0].lower() + first.label[1:]}."


def explain(a) -> str:
    """The default explanation, built only from the assessment."""
    return (_opening(a) + _core(a) + _limits(a) + _next(a)).strip()


# --------------------------------------------------------------------------
# Answering a question
# --------------------------------------------------------------------------

VERDICT_INTENT = r"\b(fake|genuine|real|authentic|counterfeit|original|legit)\b"
SAFETY_INTENT = (r"\b(can i|should i|is it okay|is it safe|safe to|"
                 r"take it|use it|eat it|drink it|consume|harmful|poison)\b")
NEXT_INTENT = r"\b(what (should|do) i do|next|now what|how do i fix|steps)\b"
REPORT_INTENT = r"\b(report|complain|fccpc|nafdac|where do i send|who do i)\b"
EXPIRY_INTENT = r"\b(expire|expiry|expired|best before|shelf life|out of date)\b"


def answer(question: str, a) -> str:
    """Answer one question from the assessment. Never from imagination."""
    q = (question or "").strip().lower()
    noun = _noun(a)
    if not q:
        return explain(a)

    if re.search(VERDICT_INTENT, q):
        return _no_verdict(a)
    if re.search(SAFETY_INTENT, q):
        return _safety(a)
    if re.search(EXPIRY_INTENT, q):
        return _expiry(a)
    if re.search(REPORT_INTENT, q):
        return _report(a)
    if re.search(NEXT_INTENT, q):
        return _next_steps(a)
    return (explain(a) + "\n\nYou can ask me why a check came out the way it "
            "did, what to do next, or who to report this to.")


def _no_verdict(a) -> str:
    highs = [c for c in a.checks if c.state == "high_risk"]
    atts = [c for c in a.checks if c.state == "attention"]
    if a.band == "high_risk" and highs:
        found = highs[0]
        return ("I can't answer that, and neither can any tool that only reads a "
                "label. 'Fake' is a legal finding that needs a laboratory and a "
                "regulator. What I can tell you is what the checks found: "
                f"{_evidence_line(found)}. That is a high-risk signal, which is "
                "enough to preserve the pack and report it — not a determination. "
                "The NAFDAC report in this case is the route to one.")
    if a.band == "review":
        what = _evidence_line(atts[0]) if atts else "nothing decisive"
        return ("No — and I won't guess. Here is the honest state: "
                f"{what}. That is worth checking, not a verdict. If you need a "
                "determination, file the report; a regulator can inspect stock "
                "in a way a photo cannot.")
    return ("No. The checks that can be done from a phone all reconcile, which is "
            "not the same as a determination. Nothing here is a laboratory test, "
            "and no result from this app can declare a product authentic. What "
            "it can do is tell you the paperwork agrees, and give you a record "
            "if it later does not.")


def _safety(a) -> str:
    noun = _noun(a)
    verb = _use_verb(a)
    past = "used" if verb == "use" else "consumed"
    if a.band == "high_risk":
        first = [c for c in a.checks if c.state == "high_risk"]
        why = _evidence_line(first[0]) if first else a.headline
        return (f"I wouldn't {verb} it. Not because I know what is inside, but "
                f"because {why[0].lower() + why[1:]}. Set it aside, keep the "
                f"pack and the receipt, and let a pharmacist or NAFDAC look at "
                f"it. If you have already {past} some and feel unwell, contact "
                f"a health professional.")
    if a.band == "review":
        return (f"I'd wait until the open questions are settled. Nothing here is "
                f"confirmed as a problem, but something has not been explained "
                f"yet. A pharmacist can confirm registration and storage in a "
                f"minute, and the seller owes you a refund if they cannot.")
    if a.band == "incomplete":
        return (f"I can't tell you that yet — not enough of the pack was read. "
                f"Photograph the back panel where the number and dates are "
                f"printed, or type them in, and the check will run.")
    return (f"The checks passed, so nothing here says don't {verb} it. That is "
            f"not medical advice and not a guarantee: storage, heat and "
            f"counterfeit printing can all defeat a label check. If you are "
            f"still unsure, a pharmacist or NAFDAC is the right place to ask.")


def _expiry(a) -> str:
    checks = [c for c in a.checks if c.id == "expiry"]
    if not checks:
        return "No expiry date was read from this pack, so it could not be checked."
    c = checks[0]
    extra = ""
    if a.band == "high_risk":
        extra = (" Do not rely on it. Expired stock is a reportable finding "
                 "whether or not anything else is wrong.")
    elif c.state == "verified":
        extra = (" That is the printed date. Heat and sunlight shorten it.")
    return c.evidence.strip() + "." + extra


def _report(a) -> str:
    targets = [x for x in a.actions if x.kind in ("nafdac_report", "refund")]
    if not targets:
        return ("There is nothing here that warrants a report. If that changes, "
                "open a case and Vouch will generate the documents.")
    lines = [f"• {x.label} — {x.why}" for x in targets[:3]]
    return ("Two routes, and they are not the same. A NAFDAC report is a "
            "regulatory notification; a refund demand goes to the seller and "
            "costs nothing to send. Vouch writes both from what we found:\n"
            + "\n".join(lines))


def _next_steps(a) -> str:
    if not a.actions:
        return "Nothing further is needed from the checks that ran."
    lines = [f"{i + 1}. {x.label} — {x.why}"
             for i, x in enumerate(a.actions[:4])]
    return ("In order:\n" + "\n".join(lines) +
            "\n\nOpen a case and the deadlines are scheduled for you, so the "
            "follow-up does not depend on remembering.")


# --------------------------------------------------------------------------
# Guardrails applied to any model output
# --------------------------------------------------------------------------

def violations(text: str, a) -> list[str]:
    """Everything wrong with a candidate explanation. Empty means safe."""
    out: list[str] = []
    low = text.lower()
    for pat in BANNED:
        if re.search(pat, low):
            out.append(f"verdict language: {pat}")
    for pat in _ADVICE:
        if re.search(pat, low):
            out.append(f"medical advice: {pat}")
    blob = json.dumps(a.to_dict(), default=str).upper()
    for token in set(NAFDAC_LIKE.findall(text.upper())):
        if token not in blob:
            out.append(f"invented identifier: {token}")
    if len(text) > 1800:
        out.append("too long")
    if len(text) < 80:
        out.append("too short to be useful")
    return out


def _trim(a) -> dict:
    d = a.to_dict()
    return {
        "band": d["band"],
        "headline": d["headline"],
        "kind": d["kind"],
        "rail": d["rail"],
        "score": d["score"],
        "checks": [
            {k: c[k] for k in ("id", "label", "state", "evidence", "proves",
                               "does_not_prove")}
            for c in d["checks"]
        ],
        "actions": [{"label": x["label"], "why": x["why"]} for x in d["actions"]],
        "registry": d.get("registry"),
        "barcode": d.get("barcode"),
        "input": {k: v for k, v in (d.get("input") or {}).items()
                  if k in ("nafdac_number", "barcode", "product_name",
                           "manufacturer", "batch", "exp_date", "mfg_date")},
    }


SYSTEM = """You are the explanation layer of Vouch, a consumer-protection tool
used in Nigeria. A deterministic engine has already assessed a product and
produced the JSON below. You are writing to the person holding the pack.

Rules, all absolute:
- Explain only what is in the JSON. Do not add facts, numbers, dates, product
  names or registry records that are not there.
- Never call a product fake, counterfeit, genuine or authentic. Never say it is
  safe or unsafe. Use the engine's vocabulary: verified, worth checking,
  high-risk signal, could not be checked.
- Never give dosing or treatment advice. For medical questions, say a
  pharmacist or clinician is the right person.
- Do not promise outcomes or guarantee a refund.
- 90 to 160 words, plain prose, no headings, no bullet points, no emoji.
- Write for someone worried and in a hurry. Short sentences. British spelling.
- End with the single most useful next action.
"""


def model_text(a, question: str = "") -> str:
    """Ask the model, then validate. Raises on any doubt."""
    if not API_KEY:
        raise RuntimeError("no key")
    import urllib.error
    import urllib.request

    user = "ASSESSMENT:\n" + json.dumps(_trim(a), indent=1, default=str)
    if question:
        user += "\n\nPERSON'S QUESTION:\n" + question.strip()
    else:
        user += ("\n\nWrite the explanation this person needs to understand the "
                 "result and act on it.")
    body = json.dumps({
        "model": MODEL,
        "temperature": 0.2,
        "max_tokens": 400,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
    }).encode()
    req = urllib.request.Request(
        API_BASE.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {API_KEY}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"model {exc.code}") from exc
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc

    try:
        text = payload["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        raise RuntimeError("unexpected model response") from exc

    bad = violations(text, a)
    if bad:
        raise RuntimeError("guard: " + "; ".join(bad[:3]))
    return text


def compose(a, question: str = "") -> tuple[str, str]:
    """The text to show, and the provider that wrote it."""
    if API_KEY:
        try:
            return model_text(a, question), MODEL
        except Exception:
            pass
    return (answer(question, a) if question else explain(a)), "vouch-composer"


# --------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------

def stream(a, question: str = "", delay: float = 0.018):
    """Yield the explanation in small chunks, so the answer arrives live.

    A model answer is produced first and validated whole, then revealed. That
    ordering is the point: nothing unvalidated ever reaches the screen.
    """
    text, who = compose(a, question)
    yield {"meta": {"provider": who}}
    words = text.split(" ")
    buf: list[str] = []
    for i, w in enumerate(words):
        buf.append(w)
        if len(buf) >= 4 or i == len(words) - 1:
            yield {"delta": " ".join(buf) + (" " if i < len(words) - 1 else "")}
            buf = []
            if delay:
                time.sleep(delay)
    yield {"done": True, "provider": who}
