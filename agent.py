"""
Vouch — conversational agent.

One request in, evidence out: the agent reads an optional pack photo,
runs the same checks as /api/check, and answers in words. It can also
PROPOSE side effects (opening a case), but it can never perform them:
proposals execute only when the user confirms, and execution is plain
code, never model output.

Two brains, same tools. With MODEL_API_KEY set, a small plan-act loop
drives the tools. Without a key, a deterministic pipeline answers from
the engine and the on-device composer. The engine owns every verdict
either way: models transcribe and explain, nothing more.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import urllib.request
from collections import Counter
from dataclasses import asdict, fields
from datetime import date

import actions
import ai
import audit
import batchdecode as BD
import cases
import engine  # noqa: F401  (re-exported for Assessment/PackInput users)
import gs1
import manufacturer as M
import mas
import napams
import playbooks
import recall
import registry as R
import scan as S
import vision
from engine import Assessment, PackInput
from knowledge import KNOWLEDGE, retrieve

API_KEY = os.environ.get("MODEL_API_KEY") or os.environ.get("META_API_KEY") or ""
API_BASE = os.environ.get("META_BASE", "https://api.meta.ai/v1")
MODEL = os.environ.get("VOUCH_MODEL", "muse-spark-1.3")

MAX_STEPS = 6
MAX_HISTORY = 10
MAX_IMAGE_B64 = 12_000_000
TIMEOUT = 45
CASE_WORDS = re.compile(r"open a case|report this|file (a )?report|escalate",
                        re.IGNORECASE)
BARCODE_SCAN = re.compile(r"\b\d{8}\b|\b\d{12,14}\b")


def capabilities() -> dict:
    return {"planner": bool(API_KEY),
            "model": MODEL if API_KEY else "vouch-composer"}


def decode_image(b64: str) -> bytes:
    """Base64 (optionally a data: URL) to bytes. ValueError when bad."""
    text = (b64 or "").strip()
    if text.startswith("data:"):
        text = text.split(",", 1)[1] if "," in text else ""
    if not text or len(text) > MAX_IMAGE_B64:
        raise ValueError("That image upload is empty or larger than ~9 MB.")
    try:
        return base64.b64decode(text, validate=True)
    except binascii.Error:
        raise ValueError("That image upload is not valid base64.")


def _clean_history(history) -> list[dict]:
    out = []
    for h in (history or []):
        if not isinstance(h, dict):
            continue
        role, text = h.get("role"), str(h.get("text") or "")[:2000]
        if role in ("user", "assistant") and text.strip():
            out.append({"role": role, "content": text})
    return out[-MAX_HISTORY:]


def _assess(pack: PackInput) -> Assessment:
    import server  # lazy: server imports agent for its endpoint
    return server._assess(
        pack, seen_batches=cases.seen_batches_for(pack.nafdac_number))


def _pack_fields(data: dict) -> PackInput:
    allowed = {f.name for f in fields(PackInput)}
    return PackInput(**{k: data[k] for k in allowed if k in data})


def _record_to_dict(res: R.Result) -> dict:
    m = res.matched
    return {
        "verdict": res.verdict,
        "message": res.message,
        "number": m.nafdac if m else "",
        "product": m.product_name if m else "",
        "manufacturer": m.manufacturer if m else "",
        "status": m.status if m else "",
        "form": m.form if m else "",
        "route": m.route if m else "",
        "strength": m.strength if m else "",
        "ingredients": m.ingredients if m else "",
        "composition": m.composition if m else "",
        "pack_size": m.pack_size if m else "",
        "approval_date": m.approval_date if m else "",
        "registration_expiry": m.registration_expiry if m else "",
        "atc": m.atc if m else "",
        "tried": res.tried,
        "signals": res.signals,
    }


def _profile_block(assessment: Assessment) -> str:
    """The full register entry in words, for number-only questions.

    Empty when no record is attached: the absence of a record must never
    read as a thin answer. Every line quotes the register; nothing here
    judges the pack in hand.
    """
    r = assessment.registry or {}
    if not r.get("nafdac"):
        return ""
    name = (r.get("product_name") or "Unnamed product").strip()
    head = f"{name} ({r['nafdac']})"
    if r.get("manufacturer"):
        head += f" by {r['manufacturer']}"
    lines = [head + "."]
    if r.get("status"):
        lines.append(f"Status {r['status']}.")
    form_bits = [b for b in (r.get("form"), r.get("route"),
                             r.get("strength")) if b]
    if form_bits:
        lines.append("; ".join(form_bits) + ".")
    ingredients = r.get("ingredients") or (r.get("composition") or "")[:160]
    if ingredients:
        lines.append(f"Contains: {ingredients}.")
    if r.get("pack_size"):
        lines.append(f"Pack: {r['pack_size']}.")
    dates = " / ".join(d for d in
                       (f"approved {r['approval_date']}" if r.get(
                           "approval_date") else "",
                        f"registration ends {r['registration_expiry']}"
                        if r.get("registration_expiry") else "") if d)
    if dates:
        lines.append(dates + ".")
    if r.get("atc"):
        lines.append(f"ATC {r['atc']}.")
    return "\n\nOn the register: " + " ".join(lines)


# --------------------------------------------------------------------------
# Tools — read-only. Side effects happen only via confirmed proposals.
# --------------------------------------------------------------------------

def _tool_check(args: dict, ctx: dict) -> dict:
    pack = _pack_fields(args.get("pack") or {})
    assessment = _assess(pack)
    ctx["pack"], ctx["assessment"] = pack, assessment
    return assessment.to_dict()


def _tool_lookup(args: dict, ctx: dict) -> dict:
    try:
        return _record_to_dict(R.lookup(args.get("number", "")))
    except R.RegistryUnavailable as exc:
        return {"verdict": "unavailable", "message": str(exc)}


def _tool_pin(args: dict, ctx: dict) -> dict:
    return mas.verify(args.get("pin", ""), args.get("shortcode", ""))


def _tool_serial(args: dict, ctx: dict) -> dict:
    return M.verify(args.get("manufacturer", ""), args.get("serial", ""))


def _tool_preview_doc(args: dict, ctx: dict) -> dict:
    builder = {"nafdac_report": actions.nafdac_report,
               "fccpc_complaint": actions.fccpc_complaint,
               "seller_demand": actions.seller_demand,
               "adr_note": actions.adr_note}.get(args.get("kind"))
    if builder is None or ctx.get("pack") is None:
        return {"error": "Run a check first, then name a document kind."}
    doc = builder(ctx["pack"], ctx["assessment"])
    return {"title": doc.title, "body": doc.body[:4000]}


def _tool_ask(args: dict, ctx: dict) -> dict:
    if ctx.get("assessment") is None:
        ctx["pack"] = PackInput()
        ctx["assessment"] = _assess(ctx["pack"])
    text, who = ai.compose(ctx["assessment"], args.get("question", ""))
    return {"answer": text, "provider": who}


TOOLS = {
    "check_pack": {
        "run": _tool_check,
        "why": "Run the full engine check on a pack.",
        "args": '{"pack": {nafdac_number, barcode, manufacturer, '
                'product_name, batch, serial, mfg_date, exp_date, '
                'seller, location}}',
    },
    "lookup_number": {
        "run": _tool_lookup,
        "why": "Resolve one NAFDAC number against the register.",
        "args": '{"number": str}',
    },
    "verify_pin": {
        "run": _tool_pin,
        "why": "Ask the maker's service about a scratch PIN.",
        "args": '{"pin": str, "shortcode": str}',
    },
    "verify_serial": {
        "run": _tool_serial,
        "why": "Check a manufacturer serial.",
        "args": '{"manufacturer": str, "serial": str}',
    },
    "preview_document": {
        "run": _tool_preview_doc,
        "why": "Draft a report or complaint from the checked pack.",
        "args": '{"kind": '
                '"nafdac_report | fccpc_complaint | seller_demand | '
                'adr_note"}',
    },
    "ask": {
        "run": _tool_ask,
        "why": "Answer a question from the current assessment.",
        "args": '{"question": str}',
    },
}


def _tool_docs() -> str:
    """One planner example line per registered tool, in order."""
    return "\n".join('{"thought": str, "tool": "%s", "args": %s}' % (
        name, entry["args"]) for name, entry in TOOLS.items())


_SYSTEM_HEAD = """You help verify medicines and goods. You have tools; the engine
behind them owns every verdict. You transcribe, explain, and propose —
you never declare a product real, fake, genuine, safe, or counterfeit
in your own words. Quote the engine's band and checks instead.
Reply with exactly one raw JSON object on every step — no code
fences, no prose, no extra text.

Tools (call one per step, strict JSON, no other keys):"""

_SYSTEM_TAIL = """Finish with {"answer": str} or, only to open a case the user asked
about or a high-risk pack needs, {"proposal": {"action": "open_case",
"pack": {...}}. The user confirms proposals; you never execute them.
A photo already read for you appears as the first tool note.
Write the answer the way a careful pharmacist speaks to a worried
shopper: plain sentences, no engine jargon, no field names, no quoted
state words. Say what was checked and what it showed, then the single
most useful next step. A number with no record might be misread, or
food or water (checked on NAFDAC's NAPAMS portal, not here), or
unregistered — say so instead of accusing. Never paste the assessment
JSON or its field names into the answer."""

SYSTEM = _SYSTEM_HEAD + "\n" + _tool_docs() + "\n\n" + _SYSTEM_TAIL


def _chat(messages: list[dict]) -> str:
    max_body = 256 * 1024  # planner replies are short
    if not (API_BASE.startswith("https://")
            or API_BASE.startswith("http://localhost")
            or API_BASE.startswith("http://127.0.0.1")):
        raise RuntimeError(
            "refusing to send the API key over unencrypted HTTP")
    payload = {"model": MODEL, "temperature": 0,
               "response_format": {"type": "json_object"},
               "messages": messages}
    req = urllib.request.Request(
        f"{API_BASE}/chat/completions", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {API_KEY}"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read(max_body + 1)
    if len(raw) > max_body:
        raise RuntimeError("model reply too large")
    return json.loads(raw.decode())["choices"][0]["message"]["content"]


def _system_prompt(message: str = "") -> str:
    """The planner briefing: rules plus knowledge, focused per message.

    Without a message this is the full standing briefing. With one, the
    matching playbook and retrieved notes join it; the core always stays,
    so the rules and facts never depend on retrieval hitting.
    """
    base = SYSTEM + "\n\n" + KNOWLEDGE
    if not (message or "").strip():
        return base
    notes = retrieve(message, include_core=False)
    if notes:
        base += "\n\nFocus notes:\n" + "\n\n".join(notes)
    brief = playbooks.briefing(message)
    if brief:
        base += "\n\n" + brief
    return base


_JSON_NUDGE = ("Your last reply was not valid action JSON. Reply with "
               "exactly one raw JSON object and nothing else.")


def _parse_action(raw: str):
    """Planner action JSON, tolerant of fenced replies.

    Returns the action dict, or None when the output is unusable
    (prose, empty, or broken JSON) so the caller falls back.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        act = json.loads(text)
    except Exception:
        return None
    return act if isinstance(act, dict) else None


def _plan(message: str, history: list[dict],
          image_bytes: bytes | None, image_type: str) -> dict:
    ctx: dict = {"pack": None, "assessment": None, "extraction": None}
    messages = [{"role": "system",
                 "content": _system_prompt(message or "")}] + history
    if image_bytes:
        ex = vision.extract_from_image(image_bytes, image_type)
        ctx["extraction"] = ex
        pack = _pack_fields(ex.to_pack_kwargs())
        ctx["pack"] = pack
        ctx["assessment"] = _assess(pack)
        messages.append({"role": "user", "content":
                         "tool:photo-read " + json.dumps({
                             "nafdac_number": ex.nafdac_number,
                             "product_name": ex.product_name,
                             "manufacturer": ex.manufacturer,
                             "batch": ex.batch, "exp_date": ex.exp_date,
                             "pin": ex.pin,
                             "band": ctx["assessment"].band})[:2000]})
    messages.append({"role": "user", "content": message or "(photo only)"})
    steps = 0
    for _ in range(MAX_STEPS):
        steps += 1
        raw = ""
        try:
            raw = _chat(messages)
            act = _parse_action(raw)
            if act is None:
                messages.append({"role": "user", "content": _JSON_NUDGE})
                raw = _chat(messages)
                act = _parse_action(raw)
            if act is None:
                raise ValueError("planner output was not action JSON")
        except Exception as exc:
            masked = re.sub(r"\d", "#", (raw or "")[:80])
            audit.log("agent_step_failed", "",
                      f"{str(exc)[:120]} | len={len(raw or '')} "
                      f"head={masked}")
            break
        if act.get("answer"):
            return _final(str(act["answer"]), ctx, steps,
                          f"meta:{MODEL}", message)
        proposal = act.get("proposal") or {}
        if proposal.get("action") == "open_case" and proposal.get("pack"):
            return _final("", ctx, steps, f"meta:{MODEL}", message,
                          confirmed_pack=_pack_fields(proposal["pack"]))
        entry = TOOLS.get(act.get("tool") or "")
        if entry is None:
            break
        try:
            result = entry["run"](act.get("args") or {}, ctx)
        except Exception as exc:
            result = {"error": f"{exc}"[:300]}
        messages.append({"role": "assistant",
                         "content": json.dumps(act)[:2000]})
        messages.append({"role": "user",
                         "content": "tool:" + json.dumps(result)[:4000]})
    return _final("", ctx, steps, f"meta:{MODEL}", message,
                      history=history)


def _pack_from_text(message: str) -> PackInput:
    """A pack carrying any registration number or barcode named in text."""
    f = _extract_fields(message or "")
    return PackInput(nafdac_number=f.get("nafdac_number", ""),
                     barcode=f.get("barcode", ""))


# --------------------------------------------------------------------------
# Conversation — the pipeline listens across turns instead of assessing
# every message in isolation.
# --------------------------------------------------------------------------

_HOWTO_SCAN = re.compile(
    r"\bhow\b[^.?!]{0,50}\bscan\b|\bscan\b[^.?!]{0,50}\bhow\b"
    r"|\bhelp\b[^.?!]{0,50}\bscan\b|\bscan\b[^.?!]{0,50}\bhelp\b",
    re.IGNORECASE)
_CHECK_IT = re.compile(r"\b(check|verify|assess)\b", re.IGNORECASE)
_RESET = re.compile(
    r"\banother\b|\bdifferent\b|\bnew pack\b|\bnext pack\b"
    r"|\bstart over\b|\bforget (it|that|this)\b", re.IGNORECASE)
_CODES_ONLY = re.compile(r"[\dA-Z\s\-/#.,:]*")
_CODE_RUN = re.compile(r"[A-Z0-9][A-Z0-9 :/\-]*[A-Z0-9]|[A-Z0-9]")
_CORRECTION = [
    re.compile(r"\b(?:this|it)(?:'s| is)\s+(?:an?\s+)?"
               r"([a-z][a-z ]*?)\s*,?\s+not\s+(?:an?\s+)?([a-z]+)",
               re.IGNORECASE),
    re.compile(r"\bno,?\s+(?:it'?s|this is|that'?s|that is)\s+"
               r"(?:an?\s+)?([a-z][a-z ]*?)(?:[.!]|$)", re.IGNORECASE),
    re.compile(r"\bactually,?\s+(?:it'?s|this is)\s+"
               r"(?:an?\s+)?([a-z][a-z ]*?)(?:[.!]|$)", re.IGNORECASE),
    re.compile(r"\bi meant\s+(?:an?\s+)?([a-z][a-z ]*?)(?:[.!]|$)",
               re.IGNORECASE),
]
_KIND_WORDS = {
    "water": "water", "sachet": "water",
    "medicine": "medicine", "drug": "medicine", "tablet": "medicine",
    "syrup": "medicine", "capsule": "medicine", "injection": "medicine",
    "juice": "beverage", "malt": "beverage", "cola": "beverage",
    "soda": "beverage", "milk": "beverage", "drink": "beverage",
    "beverage": "beverage", "tea": "beverage", "coffee": "beverage",
    "cream": "cosmetic", "lotion": "cosmetic", "soap": "cosmetic",
    "shampoo": "cosmetic", "cosmetic": "cosmetic",
    "deodorant": "cosmetic",
}
_BRAND = re.compile(r"\b([A-Z][A-Z0-9&']{2,})\b")
_BRAND_WORDS = re.compile(
    r"\b(?:called|brand(?: name)?|product(?: name)?)\s+"
    r"(?:is\s+)?([A-Za-z][\w&']*)", re.IGNORECASE)
_BRAND_IS = re.compile(
    r"\b(?:it'?s|this is|that'?s|named)\s+(?:called\s+)?"
    r"([A-Z][A-Za-z&']*(?:\s+[A-Z][A-Za-z&']*)?)")
_BRAND_STOP = {"NAFDAC", "REG", "NUMBER", "BATCH", "BARCODE", "SCAN",
               "HELP", "PLEASE", "HELLO", "DETAILS", "DETAIL", "PACK",
               "OK", "YES", "NO", "FINE", "THANKS"}
_QUESTION_INTENTS = ("VERDICT_INTENT", "SAFETY_INTENT", "EXPIRY_INTENT",
                     "REPORT_INTENT", "NEXT_INTENT")
_MADE_IT = re.compile(
    r"\bhow old\b|\bage of\b|when was (this|it|that) made\b"
    r"|\bmanufacture(d| date)?\b|\bproduction date\b",
    re.IGNORECASE)
_SHELF_LINE = ("Most sealed cosmetics keep about 3 years; the open-jar "
               "symbol (like 12M) counts from first opening.")
_PAO_M = re.compile(r"\b(\d{1,2})\s*M\b", re.IGNORECASE)
_PAO_WORDS = re.compile(r"\b(\d{1,2})\s*months?\b", re.IGNORECASE)
_PAO_CUE = re.compile(r"\bpao\b|open.jar|after opening|\bsymbol\b"
                      r"|\bjar\b|\bopened?\b|\bopening\b", re.IGNORECASE)
_MONTHS = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3,
           "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
           "july": 7, "jul": 7, "august": 8, "aug": 8,
           "september": 9, "sep": 9, "sept": 9, "october": 10,
           "oct": 10, "november": 11, "nov": 11, "december": 12,
           "dec": 12}
_OPENED = re.compile(
    r"(january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|"
    r"sept|oct|nov|dec)\s*(\d{4})?", re.IGNORECASE)
_LAST_MONTH = re.compile(r"\blast month\b", re.IGNORECASE)


_PIN_USSD = re.compile(r"\*(\d{3,6})\*\d+\*([A-Z0-9]{4,20})#")
_PIN_TO_WINDOW = re.compile(
    r"[^0-9]{0,12}(\d[\d\s-]{3,18}\d)\s+\bto\b\s+(\d{3,6})\b")
_PIN_LOOSE_WINDOW = re.compile(r"[^0-9]{0,24}?\bto\b\s*(\d{3,6})\b")
_PIN_SMS_WORDS = re.compile(r"\bsms\b|\btext\b|\bsend\b", re.IGNORECASE)
_PIN_SHORTCODE = re.compile(r"short\s?code[^\d]{0,8}(\d{3,6})",
                            re.IGNORECASE)
_PIN_HARD = re.compile(
    r"\bpin\b|\bscratch\b|\bsms\b|\btext\b|\bsend\b|verif\w*|short\s?code",
    re.IGNORECASE)
_SCRATCH = re.compile(r"\bscratch\b|\bpin\b|verification code",
                      re.IGNORECASE)
_PHOTO_WORDS = re.compile(r"\b(image|photo|picture|pic)\b", re.IGNORECASE)

_ASK_PIN = ("To check a scratch panel I need the PIN itself — scratch it "
            "off and type the number here, with the shortcode printed "
            "beside it. I'll tell you exactly what to text and help you "
            "read the reply. I still can't declare any pack genuine from "
            "here either way; the SMS check asks the maker's own service.")

_PIN_ACK = "Got it — I'll use that PIN for the scratch check."

_PIN_NOT_SEEN = ("I looked at the photo but couldn't make out PIN digits "
                 "on it. Is the silver scratch strip already off? If it "
                 "is, type the digits here, or send a close-up that fills "
                 "the frame with the panel. If the strip is still on, "
                 "scratch it off first — no photo can read through it.")

_REACTION = re.compile(
    r"\b(side effects?|adverse|reaction|rashes|\brash\b|hives"
    r"|swell(ing|en)?|itch(y|ing)?|vomit(ed|ing)?|nausea"
    r"|dizz(y|iness)|breathless|palpitat|bleed(ing)?)\b",
    re.IGNORECASE)

_ADR_TEXT = ("I'm sorry you're dealing with that. I can't give medical "
            "advice — please speak to a pharmacist or clinician about "
            "what happened, and seek care first if anyone is seriously "
            "unwell. I can prepare an adverse-reaction note for NAFDAC "
            "from this pack: open a case and choose the ADR note, or "
            "report directly in the Med Safety App, by email to "
            "pharmacovigilance@nafdac.gov.ng, or on 0800-1-NAFDAC "
            "(0800-1-623322).")


def _mask_runs(msg: str, values) -> str:
    """Blank runs already claimed as a PIN/shortcode, in place.

    Each value is matched literally, allowing printed separators between
    its characters; longer values go first so a shortcode never eats a
    piece of its own PIN.
    """
    for v in sorted((x for x in values if x), key=len, reverse=True):
        pat = ("(?<![A-Z0-9])"
               + r"[\s-]*".join(re.escape(c) for c in v)
               + "(?![A-Z0-9])")
        msg = re.sub(pat, lambda m: " " * len(m.group(0)), msg,
                     flags=re.IGNORECASE)
    return msg


def _chat_pin(msg: str) -> tuple[str, str]:
    """A scratch PIN + shortcode typed in chat. Label-driven, else nothing.

    Labelled PINs reuse the OCR reader's rule (scan.live_pins, strong
    reads only); SMS instructions and USSD dial strings have their own
    shapes because the shortcode pattern for print would read the PIN's
    own head as the shortcode.
    """
    msg = msg or ""
    m = _PIN_USSD.search(msg)
    if m:
        return re.sub(r"[\s-]+", "", m.group(2)).upper(), m.group(1)
    shortcode = ""
    for sm in _PIN_SMS_WORDS.finditer(msg):
        window = msg[sm.end():sm.end() + 48]
        m = _PIN_TO_WINDOW.match(window)
        if m:
            pin = re.sub(r"[\s-]+", "", m.group(1)).upper()
            if 4 <= len(pin) <= 20:
                return pin, m.group(2)
            continue
        m = _PIN_LOOSE_WINDOW.match(window)
        if m and not shortcode:
            shortcode = m.group(1)
    pin = ""
    hard = list(_PIN_HARD.finditer(msg))
    if hard:
        tail = msg[hard[0].start():]
        for hit in S.live_pins(tail):
            if hit["strength"] == "strong" and hit["value"] != shortcode:
                pin = hit["value"]
                break
    if not shortcode:
        m = _PIN_SHORTCODE.search(msg)
        if m:
            shortcode = m.group(1)
    if pin and pin == shortcode:
        pin = ""
    return pin, shortcode


def _history_pin(history) -> tuple[str, str]:
    """A PIN named earlier in the chat. Either side may carry it."""
    pin, shortcode = "", ""
    for h in _clean_history(history):
        f = _extract_fields(h.get("content", ""))
        if f.get("pin"):
            pin = f["pin"]
        if f.get("shortcode"):
            shortcode = f["shortcode"]
    return pin, shortcode


_VT_USSD = re.compile(r"\*(\d{3,6})\*")
_VT_SMS = re.compile(r"\b(?:SMS|TEXT|SEND)\b[^0-9]{0,20}(\d{3,6})(?![\d])",
                     re.IGNORECASE)


def _resolve_shortcode(field_sc: str, verify_text: str):
    """Which shortcode to text: the panel's own instructions win ties.

    Returns (shortcode, qualifier). The qualifier is empty when the
    field and the instructions agree — otherwise it names the doubt so
    the user confirms the digits before anything is sent.
    """
    cands = []
    if verify_text:
        cands = (_VT_USSD.findall(verify_text)
                 + _VT_SMS.findall(verify_text))
    field_sc = field_sc or ""
    if field_sc and field_sc in cands:
        return field_sc, ""
    if field_sc and not cands:
        return field_sc, ""
    if not cands:
        return "", ""
    top, count = Counter(cands).most_common(1)[0]
    if count >= 2:
        if not field_sc:
            return top, ""
        return top, (f" (I also read {field_sc} on the same label — "
                     f"confirm {top} matches the shortcode printed "
                     "beside the panel.)")
    if not field_sc:
        return top, (f" (I only caught {top} once on the label — "
                     "confirm it against the shortcode printed beside "
                     "the panel.)")
    return field_sc, (f" (I also read {top} on the same label — "
                      f"confirm {field_sc} matches the shortcode "
                      "printed beside the panel.)")


def _provider_hint() -> str:
    """Named shortcodes from photographed panels. Hints, never defaults."""
    bits = [f"{name.title()} panels usually show {sc}"
            for name, sc in sorted(mas.SHORTCODE_HINTS.items())]
    if not bits:
        return ""
    text = (" " + "; ".join(bits) + " — if yours matches, that is the "
            "one; otherwise go by what is printed beside your panel.")
    reported = sorted((sc, name)
                      for sc, name in mas.REPORTED_SHORTCODES.items()
                      if sc not in mas.SHORTCODE_HINTS.values())
    if reported:
        text += (" Published (not yet photographed here): "
                 + ", ".join(f"{sc} ({name})" for sc, name in reported)
                 + ".")
    return text


def _mas_note(pin: str, shortcode: str, verify_text: str = "") -> str:
    """Deterministic scratch-PIN guidance. Empty when there is no PIN."""
    if not pin:
        return ""
    shortcode, qualifier = _resolve_shortcode(shortcode or "",
                                              verify_text or "")
    res = mas.verify(pin, shortcode)
    if res.get("connected") and res.get("verdict") == "suspicious":
        return ("\n\nThe maker's verification service"
                f" ({res.get('provider') or 'unknown provider'}) flags "
                f"this PIN: {res.get('detail') or 'suspicious'}. That is "
                "the service's statement about the PIN, not this app's. "
                "Keep the pack, the PIN and that reply together."
                + qualifier)
    if res.get("connected") and res.get("verdict") == "authentic":
        return (f"\n\n{res.get('provider') or 'The verification service'} "
                f"replies: {res.get('detail') or 'the PIN checks out'}. "
                "That is the service's statement about the PIN — it speaks "
                "for the code, not for what is inside the pack."
                + qualifier)
    if shortcode:
        base = (f"\n\nThat scratch panel is verifiable. Text {pin} to "
                f"{shortcode} (free) — the maker's service replies about "
                "that PIN. Paste its exact reply here and I'll help you "
                "read it.")
    else:
        base = ("\n\nThat scratch panel is verifiable. Text the PIN to the "
                "shortcode printed beside the panel — the maker's service "
                "replies about that PIN. Paste its exact reply here and "
                "I'll help you read it." + _provider_hint())
    if res.get("connected") and res.get("detail"):
        base += f" (The connected service says: {res.get('detail')})"
    return base + qualifier


_REPLY_CUE = re.compile(
    r"\bsay\b|\bsays\b|\bsaid\b|\breply\b|\breplied\b|\bresponse\b|"
    r"\btold\b|\breads\b|\bshows\b|\bmessage\b", re.IGNORECASE)
_REPLY_VERDICT = re.compile(
    r"valid|invalid|genuine|original|authentic|congratulat\w*|"
    r"\bused\b|expired|record|fail\w*|\bpass\w*|legit\w*|"
    r"counterfeit|fake|sorry|verif\w*ed\b|error\w*", re.IGNORECASE)
_REPLY_FAIL_HARD = re.compile(
    r"invalid|not valid|not genuine|not authentic|not original|"
    r"inauthentic|unverified|no record|could not verify|"
    r"unable to verify|unsuccessful|"
    r"expired|counterfeit|fake|sorry|fail\w*", re.IGNORECASE)
_REPLY_FRESH = re.compile(
    r"never been used|never used|not been used|not used\b|unused",
    re.IGNORECASE)
_REPLY_PASS = re.compile(
    r"congratulat\w*|genuine|original(?!\s+(packag|box|pack|seal))|"
    r"authentic|legit\w*|verif\w*ed\b|\bpass\w*|\bvalid\b",
    re.IGNORECASE)
_REPLY_NARRATION = re.compile(r"\bi\b[^.?!]{0,30}\bused\b", re.IGNORECASE)
_REPLY_OPINION = re.compile(
    r"\b(feel|feels|felt|look|looks|looked|seem|seems|sound|taste)\b"
    r"[^.?!]{0,20}\b(genuine|original|authentic|real)\b", re.IGNORECASE)
_REPLY_QUESTION = re.compile(
    r"^(is|are|was|were|do|does|did|can|could|will|would|what|when|"
    r"where|which|who|how|why)\b", re.IGNORECASE)
_REPLY_REQUEST = re.compile(
    r"\bconfirms?\b|\bcheck\b|\bverify\b|\bif\b|\bwhether\b",
    re.IGNORECASE)

_REPLY_PASS_TEXT = ("That reply reads as a pass from the maker's service — "
                    "it says the PIN checks out. That speaks for the code "
                    "on the panel, not for what's inside the pack, so keep "
                    "the pack, the receipt and that reply together.")
_REPLY_FAIL_TEXT = ("That reply reads as a fail from the maker's service. "
                    "Don't buy or use that pack — keep the pack, the PIN "
                    "and the exact reply together as evidence. First "
                    "double-check you texted every digit exactly as "
                    "printed — one wrong digit fails the same way a bad "
                    "PIN does.")
_REPLY_UNCLEAR_TEXT = ("I can't tell from that whether the service passed "
                       "or failed the PIN — paste the service's full exact "
                       "reply. I'm looking for words like valid, invalid, "
                       "used or expired.")


def _read_reply(message: str, fresh_pin: bool = False):
    """A pasted verification-service reply: pass, fail, unclear, or None.

    Only verdict language counts, never a bare question — and a verdict
    glued to a PIN the user just typed needs quoting words, otherwise
    the user's own claim would read as the service's answer.
    """
    msg = (message or "").strip()
    if not msg or not _REPLY_VERDICT.search(msg):
        return None
    if _REPLY_REQUEST.search(msg):
        return None
    if msg.endswith("?") or _REPLY_QUESTION.match(msg):
        return None
    if _REPLY_OPINION.search(msg):
        return None
    if len(msg.split()) > 14 and not _REPLY_CUE.search(msg):
        return None
    if fresh_pin and not _REPLY_CUE.search(msg):
        return None
    if _REPLY_NARRATION.search(msg):
        return None
    if _REPLY_FAIL_HARD.search(msg):
        return "fail"
    if _REPLY_FRESH.search(msg):
        return "pass"
    if re.search(r"\bused\b", msg, re.IGNORECASE):
        return "fail"
    if _REPLY_PASS.search(msg):
        return "pass"
    return "unclear"


def _extract_fields(text: str) -> dict:
    """Pack details named in one message: number, barcode, batch, brand.

    Batch and date codes are the trap: "BB 290927AJ57" contains a run the
    number pattern would otherwise accept while ignoring the trailing
    letters. So a candidate only counts as a NAFDAC number when it is a
    whole token (not a fragment of a longer code) with a series shape,
    plus a dash, an explicit label, or a message that is only codes.
    """
    out: dict = {"nafdac_number": "", "barcode": "", "batch": "",
                 "product_name": "", "kind_word": "", "denied": "",
                 "correction": False, "pin": "", "shortcode": ""}
    msg = text or ""
    pin, shortcode = _chat_pin(msg)
    out["pin"], out["shortcode"] = pin, shortcode
    if pin or shortcode:
        msg = _mask_runs(msg, (pin, shortcode))
    up = msg.upper()
    squashed = re.sub(r"(?<=\d) (?=\d)", "", msg)
    batch_word = bool(re.search(
        r"\bbatch\b|\blot\s*(?:no\.?|number|#|:)?\s*[A-Z0-9]", msg,
        re.IGNORECASE))
    code_word = bool(re.search(r"\bbarcode\b|\bgtin\b|\bean\b", msg,
                               re.IGNORECASE))
    batch_context = batch_word and not code_word
    if not batch_context:
        m = BARCODE_SCAN.search(squashed)
        if m:
            out["barcode"] = re.sub(r"\s+", "", m.group(0))
    codes_only = (bool(re.fullmatch(_CODES_ONLY, up)) and bool(
        re.search(r"\d", up)) and not batch_context)
    out["nafdac_number"] = R.extract_number(msg, codes_only=codes_only)
    if not out["nafdac_number"] and not out["barcode"]:
        for run in _CODE_RUN.findall(msg):
            letters = re.search(r"[A-Z]", run)
            digits = re.search(r"\d", run)
            if letters and digits and len(run.strip()) >= 4:
                out["batch"] = run.strip().upper()
                break
        if not out["batch"]:
            if batch_context:
                for run in re.findall(r"\d(?:[\d ]*\d)?", msg):
                    digits = re.sub(r"\s+", "", run)
                    if 4 <= len(digits) <= 20:
                        out["batch"] = digits
                        break
            if not out["batch"]:
                for tok in re.findall(r"\S+", msg):
                    clean = tok.strip(" .,;:#")
                    if (len(clean) >= 4
                            and re.search(r"[A-Za-z]", clean)
                            and re.search(r"\d", clean)
                            and re.fullmatch(r"[A-Za-z0-9:/#.,-]+",
                                             clean)):
                        out["batch"] = clean.upper()
                        break
            if not out["batch"] and codes_only:
                pure_words = re.findall(r"[A-Z]+", up)
                if not any(len(t) >= 3 for t in pure_words):
                    code = re.sub(r"\s+", " ", up).strip(" .,;:#")
                    if (re.search(r"[A-Z]", code) and re.search(r"\d", code)
                            and len(code) >= 4
                            and not re.search(r"\d{8,}", code)):
                        out["batch"] = code
    for pat in _CORRECTION:
        m = pat.search(msg)
        if not m:
            continue
        claim, denied = m.group(1), (m.group(2) or "")
        kind = ""
        for word in claim.lower().split():
            if word in _KIND_WORDS:
                kind = _KIND_WORDS[word]
        if kind or denied:
            out["correction"] = True
            out["kind_word"] = kind
            out["denied"] = denied.lower()
            if kind and not out["product_name"]:
                out["product_name"] = kind
            break
        raw = re.findall(r"[A-Za-z][\w&']*", m.group(1))
        if raw and raw[-1][0].isupper():
            out["correction"] = True
            out["product_name"] = raw[-1].capitalize()
            break
    if not out["kind_word"]:
        words = re.findall(r"[A-Za-z]+", msg.lower())
        for i, word in enumerate(words):
            if word in _KIND_WORDS and not (
                    i and words[i - 1] in ("not", "no", "never")):
                out["kind_word"] = _KIND_WORDS[word]
                break
    brand = ""
    for tok in _BRAND.findall(msg):
        if not re.search(r"\d", tok) and tok not in _BRAND_STOP:
            brand = tok.capitalize()
            break
    if not brand:
        m = _BRAND_WORDS.search(msg)
        if m and m.group(1).upper() not in _BRAND_STOP:
            brand = m.group(1).capitalize()
    if not brand:
        m = _BRAND_IS.search(msg)
        if m:
            words = m.group(1).split()
            if (m.group(1).upper() not in _BRAND_STOP
                    and all(w.lower() not in _KIND_WORDS for w in words)):
                brand = " ".join(w.capitalize() for w in words)
    if brand:
        kind = out["kind_word"]
        out["product_name"] = f"{brand} {kind}" if kind else brand
    return out


def _merge(pack: PackInput, f: dict) -> bool:
    """Fold one message's fields into the working pack. True if new."""
    changed = False
    for key in ("nafdac_number", "barcode", "batch"):
        if f.get(key) and getattr(pack, key) != f[key]:
            setattr(pack, key, f[key])
            changed = True
    name = f.get("product_name") or ""
    if name and pack.product_name != name:
        if not f.get("kind_word"):
            old_kind = next(
                (w for w in pack.product_name.lower().split()
                 if w in _KIND_WORDS.values()), "")
            if old_kind and old_kind not in name.lower():
                name = f"{name} {old_kind}"
        pack.product_name = name
        changed = True
    return changed


def _working_pack(history, message: str) -> tuple[PackInput, dict, bool]:
    """The pack across turns: history first, this message last.

    Also reports whether this message added anything new, so a repeated
    detail gets noticed instead of re-acknowledged.
    """
    pack = PackInput()
    if not _RESET.search(message or ""):
        for h in _clean_history(history):
            if h.get("role") == "user":
                _merge(pack, _extract_fields(h.get("content", "")))
    fresh = _extract_fields(message or "")
    changed = _merge(pack, fresh)
    return pack, fresh, changed


def _have_line(pack: PackInput) -> str:
    bits = []
    if pack.product_name:
        bits.append(pack.product_name)
    if pack.nafdac_number:
        bits.append(f"NAFDAC {pack.nafdac_number}")
    if pack.barcode:
        bits.append(f"barcode {pack.barcode}")
    if pack.batch:
        bits.append(f"batch {pack.batch}")
    if not bits:
        return ""
    return "So far I have " + ", ".join(bits) + ". "


def _next_line(assessment: Assessment, history=None) -> str:
    """The next step, rotating past suggestions already given.

    Without the rotation every fragment turn ends on the same sentence,
    which reads as the agent talking in a loop.
    """
    acts = assessment.actions or []
    if not acts:
        return assessment.headline
    said = ""
    for h in reversed(_clean_history(history)):
        if h.get("role") == "assistant" and h.get("content"):
            said = h["content"]
            break
    for act in acts:
        if act.label not in said:
            return f"Next: {act.label} — {act.why}"
    return ""


def _tail(pack: PackInput, assessment: Assessment, history=None) -> str:
    have = _have_line(pack)
    nxt = _next_line(assessment, history)
    if nxt:
        return f"{have}{nxt}"
    return (f"{have}That's everything until more of the pack arrives — "
            "send the NAFDAC number, dates or a photo.")


def _is_question(message: str) -> bool:
    q = (message or "").lower()
    return any(re.search(getattr(ai, name), q)
               for name in _QUESTION_INTENTS)


def _converse(pack: PackInput, fresh: dict, message: str,
              assessment: Assessment, history=None) -> str | None:
    """A conversational reply, or None when a full check is wanted."""
    gtext = re.sub(r"^[Gg]\s+", "", (message or "").strip())
    if gs1.is_gs1(gtext):
        return "Decoded pack code: " + gs1.summarize(gs1.parse(gtext))
    small = ai._small_talk(message or "")
    if small is not None:
        return small
    if _HOWTO_SCAN.search(message or ""):
        return ("To scan: open the Scan tab and point the camera at the "
                "barcode until it reads — or just type the digits here "
                "and I'll take it from there. The barcode alone can't "
                "confirm a pack, so I'll still need the NAFDAC number or "
                "the dates after.")
    _hpin, _hsc = _history_pin(history)
    if _SCRATCH.search(message or "") and not (fresh.get("pin") or _hpin):
        if _PHOTO_WORDS.search(message or ""):
            return _PIN_NOT_SEEN
        return _ASK_PIN
    if fresh.get("shortcode") and not (fresh.get("pin") or _hpin):
        return (f"Got the shortcode — {fresh['shortcode']}. Now type "
                "the scratch PIN itself and I'll tell you what to "
                "text.")
    if fresh.get("pin") or _hpin:
        _reply = _read_reply(message, bool(fresh.get("pin")))
        if _reply == "pass":
            return _REPLY_PASS_TEXT
        if _reply == "fail":
            return _REPLY_FAIL_TEXT
        if _reply == "unclear":
            return _REPLY_UNCLEAR_TEXT
    _nap = _napams_answer(message, pack, history)
    if _nap is not None:
        return _nap
    if _REACTION.search(message or ""):
        return _ADR_TEXT
    if fresh.get("correction"):
        name = pack.product_name or "that"
        denied = fresh.get("denied") or ""
        ack = (f"Got it — {name}, not {denied}. I've updated that."
               if denied else f"Got it — {name}. I've updated that.")
        return f"{ack} {_tail(pack, assessment, history)}"
    if _MADE_IT.search(message or "") and pack.batch:
        return _made_answer(pack)
    if not CASE_WORDS.search(message or ""):
        months, opened, cued = _pao_context(history, message)
        if cued and months and opened:
            return _pao_answer(months, opened)
        if cued and months and not opened:
            return (f"{months}M — got it. What month did you open it?")
        if cued and opened and not months:
            return ("Got the opening month. What does the open-jar symbol "
                    "say — 6M, 12M, 24M?")
    _verdict_asked = bool(re.search(ai.VERDICT_INTENT,
                                    (message or "").lower()))
    if (_is_question(message) or _verdict_asked) and (
            pack.nafdac_number or pack.barcode or pack.batch
            or pack.product_name or fresh.get("pin") or _hpin):
        return ai.answer(message, assessment) + _profile_block(assessment)
    if _CHECK_IT.search(message or "") and not (
            pack.nafdac_number or pack.barcode or pack.batch):
        return ("I don't have anything on this pack yet. Send the NAFDAC "
                "number, barcode, batch or a photo of the back panel and "
                "I'll check it.")
    return None


_LEAK = re.compile(r"engine band\s*=|high_risk|\"checks\"|'checks':"
                     r"|severity\s*[:=]")


def _final(answer: str, ctx: dict, steps: int, provider: str,
           message: str, confirmed_pack: PackInput | None = None,
           history=None) -> dict:
    if ctx.get("assessment") is None:
        ctx["pack"] = _pack_from_text(message)
        ctx["assessment"] = _assess(ctx["pack"])
    if answer and _LEAK.search(answer):
        audit.log("agent_leak_fallback", "", answer[:120])
        answer = ""
    if not answer:
        _ex0 = ctx.get("extraction")
        _mf = _extract_fields(message or "")
        _pin = (_ex0.pin if _ex0 else "") or _mf.get("pin", "")
        _hpin, _hsc = _history_pin(history) if history else ("", "")
        if not _pin and (_SCRATCH.search(message or "")
                         or _mf.get("shortcode")):
            _pin = _hpin
        if _SCRATCH.search(message or "") and not _pin:
            if _PHOTO_WORDS.search(message or ""):
                answer, provider = _PIN_NOT_SEEN, "vouch-composer"
            else:
                answer, provider = _ASK_PIN, "vouch-composer"
        else:
            _reply = _read_reply(message, bool(_mf.get("pin"))) if _pin else None
            if _reply == "pass":
                answer, provider = _REPLY_PASS_TEXT, "vouch-composer"
            elif _reply == "fail":
                answer, provider = _REPLY_FAIL_TEXT, "vouch-composer"
            elif _reply == "unclear":
                answer, provider = _REPLY_UNCLEAR_TEXT, "vouch-composer"
            else:
                _sc = (_ex0.shortcode if _ex0 else "") or _mf.get(
                    "shortcode", "")
                if not _sc and (_pin or _SCRATCH.search(message or "")):
                    _sc = _hsc
                _pack0 = ctx.get("pack")
                _ack = _pin_ack_text(
                    _pack0, ctx["assessment"], _pin, _sc,
                    _ex0.verify_text if _ex0 else "")
                if _ack is not None:
                    answer, provider = _ack, "vouch-composer"
                else:
                    answer, provider = ai.compose(ctx["assessment"], message)
                    answer += _profile_block(ctx["assessment"])
                    if _ex0 is not None and _extraction_empty(_ex0):
                        answer = _PHOTO_UNREADABLE + answer
                    answer += _mas_note(
                        _pin, _sc, _ex0.verify_text if _ex0 else "")
    pack = confirmed_pack or ctx["pack"]
    proposal = None
    if pack is not None and (pack.nafdac_number or pack.barcode):
        fresh = _assess(pack)
        asked = (confirmed_pack is not None
                 or CASE_WORDS.search(message or ""))
        photographed = (confirmed_pack is None
                        and ctx.get("extraction") is not None)
        if fresh.band == "high_risk" and (asked or photographed):
            proposal = _proposal(pack, fresh)
    ex = ctx.get("extraction")
    return {"answer": answer, "proposal": proposal,
            "extraction": ex.to_dict() if ex else None,
            "assessment": ctx["assessment"].to_dict(),
            "provider": provider, "steps": steps}


def _proposal(pack: PackInput, assessment: Assessment) -> dict:
    return {"action": "open_case",
            "title": f"Open a case: {assessment.headline[:80]}",
            "detail": (f"{assessment.band}: {assessment.headline} "
                       f"Pack {pack.nafdac_number or pack.barcode}."),
            "pack": asdict(pack)}


def _parse_pao(text: str) -> tuple[int | None, date | None]:
    """PAO months and opening month from one message, if stated."""
    msg = text or ""
    months: int | None = None
    m = _PAO_M.search(msg)
    if m:
        months = int(m.group(1))
    elif _PAO_WORDS.search(msg) and _PAO_CUE.search(msg):
        months = int(_PAO_WORDS.search(msg).group(1))  # type: ignore[union-attr]
    if months is not None and not 1 <= months <= 60:
        months = None
    opened: date | None = None
    today = date.today()
    if _LAST_MONTH.search(msg):
        first = date(today.year, today.month, 1)
        opened = date.fromordinal(first.toordinal() - 1).replace(day=1)
    else:
        m = _OPENED.search(msg)
        if m and ("open" in msg.lower() or months is not None):
            month = _MONTHS[m.group(1).lower()]
            year = int(m.group(2)) if m.group(2) else today.year
            if not m.group(2) and month > today.month:
                year -= 1
            opened = date(year, month, 1)
    return months, opened


def _bare_month(text: str, today: date) -> date | None:
    m = _OPENED.search(text or "")
    if not m:
        return None
    month = _MONTHS[m.group(1).lower()]
    year = int(m.group(2)) if m.group(2) else today.year
    if not m.group(2) and month > today.month:
        year -= 1
    try:
        return date(year, month, 1)
    except ValueError:
        return None


def _pao_context(history, message: str
                 ) -> tuple[int | None, date | None, bool]:
    """PAO facts across turns, plus whether PAO was ever cued.

    A bare month ("June") only counts as an opening month when the
    jar symbol is already on the table — otherwise "I ran 5M in June"
    would read as cosmetics.
    """
    texts: list[str] = []
    if not _RESET.search(message or ""):
        texts = [h.get("content", "") for h in _clean_history(history)
                 if h.get("role") == "user"]
    texts.append(message or "")
    months: int | None = None
    opened: date | None = None
    for text in texts:
        pm, po = _parse_pao(text)
        months = pm if pm is not None else months
        opened = po if po is not None else opened
    cue = any(_PAO_CUE.search(t) for t in texts)
    if months is not None and opened is None:
        today = date.today()
        for text in reversed(texts):
            bare = _bare_month(text, today)
            if bare is not None:
                opened = bare
                break
    return months, opened, cue


def _pao_answer(months: int, opened: date) -> str:
    s = BD.pao_status(months, opened)
    base = (f"Counted from {opened.strftime('%b %Y')}, {months}M runs "
            f"out in {s['use_until_label']}. ")
    if s["state"] == "passed":
        return base + (f"That date has passed ({-s['days_left']} days "
                       "ago), so the maker no longer stands behind this "
                       "pack. If it looks, smells or feels off, stop "
                       "using it and ask a pharmacist.")
    if s["state"] == "almost":
        return base + (f"About {s['days_left']} days left — plan the "
                       "replacement.")
    return base + "Still within the symbol."


def _made_answer(pack: PackInput) -> str:
    """When this pack was made, from its batch code — or why that is
    not knowable. Decodes are quoted with their ambiguity, never bare."""
    dec = BD.decode(f"{pack.product_name} {pack.manufacturer}", pack.batch)
    if dec.confidence == "decoded" and dec.made:
        age = dec.age_years() or 0
        if age >= 1:
            old = f"That makes it about {age:.0f} years old. "
        elif age * 12 >= 1:
            old = f"That makes it about {age * 12:.0f} months old. "
        else:
            old = "That makes it only weeks old. "
        return (f"By {dec.brand_label}'s own batch pattern, this pack was "
                f"made around {dec.made_label}: {dec.method} {old}"
                f"{dec.note} {_SHELF_LINE}")
    return dec.note


def _fragment_ack(pack: PackInput, fresh: dict,
                  assessment: Assessment, history=None) -> str | None:
    """Acknowledge newly arrived details without a verdict dump."""
    bits = []
    if fresh.get("barcode"):
        bits.append(f"Barcode {fresh['barcode']} noted.")
    if fresh.get("batch"):
        code = fresh["batch"]
        if re.match(r"\d{6}[A-Z]?", code):
            bits.append(f"That reads like a date or batch code ({code}) "
                        "— I've kept it with this pack.")
        else:
            bits.append(f"Batch {code} recorded.")
        brand = f"{pack.product_name} {pack.manufacturer}".strip()
        if brand:
            dec = BD.decode(brand, code)
            if dec.confidence == "decoded" and dec.made:
                bits.append(f"By {dec.brand_label}'s pattern, made around "
                            f"{dec.made_label}.")
    if fresh.get("product_name") and not fresh.get("correction"):
        bits.append(f"Noted — {pack.product_name}.")
    if not bits:
        return None
    return " ".join(bits) + f" {_tail(pack, assessment, history)}"


_PHOTO_FIELDS = ("nafdac_number", "barcode", "product_name", "manufacturer",
                 "batch", "exp_date", "mfg_date", "pin")

# Model tamper signals become engine flags at confidence 0.6 and up.
# impossible_date is owned by the expiry engine via the transcribed date;
# "other" stays visible in the extraction but carries no engine weight.
_TAMPER_FLAGS = {"sticker_edge": "sticker_over_print",
                 "print_mismatch": "print_mismatch",
                 "residue": "print_residue",
                 "misalignment": "print_misaligned"}

_PHOTO_UNREADABLE = (
    "I received your photo, but I could not read the label from it — "
    "no name, number, date or batch came through. "
    "Fill the frame with one panel, hold still, and keep glare off the "
    "print, then send it again. ")


def _extraction_empty(ex) -> bool:
    """Nothing usable came off the photo: say so, don't ignore it."""
    if getattr(ex, "tamper_signals", None):
        return False
    return not any(getattr(ex, f, "") for f in _PHOTO_FIELDS)


def _recover_pin(message: str, fresh: dict, history) -> tuple[str, str]:
    """PIN + shortcode for this turn: fresh first, history fills gaps."""
    pin = fresh.get("pin") or ""
    sc = fresh.get("shortcode") or ""
    if not pin or not sc:
        hpin, hsc = _history_pin(history)
        if not pin and (_SCRATCH.search(message or "") or sc):
            pin = hpin
        if not sc and (pin or _SCRATCH.search(message or "")):
            sc = hsc
    return pin, sc


def _pin_note(message: str, fresh: dict, history) -> str:
    """Scratch-PIN guidance for this turn. Empty when there is no PIN,
    or when the turn itself reads the service's reply."""
    pin, sc = _recover_pin(message, fresh, history)
    if pin and _read_reply(message, bool(fresh.get("pin"))):
        return ""
    return _mas_note(pin, sc)


def _pin_ack_text(pack, assessment, pin: str, sc: str,
                  verify_text: str = "") -> str | None:
    """Short PIN acknowledgment when the assessment has nothing else
    to say. None when a full check is wanted."""
    if (pin and assessment.band == "incomplete"
            and pack is not None
            and not (pack.nafdac_number or pack.barcode)):
        return _PIN_ACK + _mas_note(pin, sc, verify_text or "")
    return None


def _fast_answer(message: str, history=None) -> dict | None:
    """Instant deterministic answer, or None when planning is needed.

    Text-only turns the deterministic layer already answers well —
    small talk, scratch protocol, corrections, pack questions — skip
    the model entirely. Photos, fresh checks, and case words always
    plan.
    """
    if CASE_WORDS.search(message or ""):
        return None
    pack, fresh, changed = _working_pack(history, message)
    if fresh.get("nafdac_number"):
        return None
    assessment = _assess(pack)
    said = _converse(pack, fresh, message, assessment, history)
    if said is not None:
        text = said
    elif not changed and any(fresh.get(k) for k in
                             ("nafdac_number", "barcode", "batch",
                              "product_name")):
        text = ("Already have that. "
                f"{_tail(pack, assessment, history)}")
    else:
        ack = _fragment_ack(pack, fresh, assessment, history)
        if ack is not None:
            text = ack
        else:
            pin, sc = _recover_pin(message, fresh, history)
            pin_text = _pin_ack_text(pack, assessment, pin, sc)
            if pin_text is None:
                return None
            return {"answer": pin_text, "proposal": None,
                    "extraction": None,
                    "assessment": assessment.to_dict(),
                    "provider": "vouch-composer", "steps": 0}
    text += _pin_note(message, fresh, history)
    return {"answer": text, "proposal": None, "extraction": None,
            "assessment": assessment.to_dict(),
            "provider": "vouch-composer", "steps": 0}


def _pipeline(message: str, image_bytes: bytes | None,
              image_type: str, history=None) -> dict:
    pack, fresh, changed = _working_pack(history, message)
    has_history = bool(_clean_history(history))
    extraction = None
    if image_bytes:
        try:
            extraction = vision.extract_from_image(image_bytes, image_type)
        except vision.VisionUnavailable as exc:
            assessment = _assess(pack)
            return {"answer": str(exc) + " Or type what the pack says.",
                    "proposal": None, "extraction": None,
                    "assessment": assessment.to_dict(),
                    "provider": "vouch-composer", "steps": 1}
        for key, val in extraction.to_pack_kwargs().items():
            if val and hasattr(pack, key):
                setattr(pack, key, val)
        # Agent packs start flagless; vision tamper is the only source here.
        _tflags = sorted({_TAMPER_FLAGS[s.signal]
                          for s in extraction.tamper_signals
                          if s.signal in _TAMPER_FLAGS
                          and (s.confidence or 0.0) >= 0.6})
        if _tflags:
            pack.flags = _tflags
            pack.flags_source = "vision"
        assessment = _assess(pack)
        text, who = ai.compose(assessment, message)
        if _extraction_empty(extraction):
            text = _PHOTO_UNREADABLE + text
        _pin = extraction.pin or fresh.get("pin") or ""
        _sc = extraction.shortcode or fresh.get("shortcode") or ""
        if _pin and not _sc:
            _, _sc = _history_pin(history)
        text += _mas_note(_pin, _sc, extraction.verify_text or "")
    else:
        assessment = _assess(pack)
        said = _converse(pack, fresh, message, assessment, history)
        repeated = (not changed and any(
            fresh.get(k) for k in ("nafdac_number", "barcode", "batch",
                                   "product_name")))
        if said is not None:
            text, who = said, "vouch-composer"
        elif repeated:
            text, who = ("Already have that. "
                         f"{_tail(pack, assessment, history)}",
                         "vouch-composer")
        elif (fresh.get("nafdac_number")
                or (_CHECK_IT.search(message or "")
                    and (pack.nafdac_number or pack.barcode
                         or pack.batch))
                or (not has_history and fresh.get("barcode"))):
            text, who = ai.compose(assessment, message)
            text += _profile_block(assessment)
        else:
            ack = _fragment_ack(pack, fresh, assessment, history)
            if ack is not None:
                text, who = ack, "vouch-composer"
            elif _have_line(pack):
                text, who = (_have_line(pack) + "What would you like to "
                             "know — why a check came out as it did, what "
                             "to do next, or who to report to?",
                             "vouch-composer")
            else:
                text, who = ("I didn't catch a pack detail in that. Send "
                             "the NAFDAC number, barcode, batch or a "
                             "photo — or say 'help' and I'll walk you "
                             "through it.", "vouch-composer")
    text += _pin_note(message, fresh, history)
    proposal = None
    if (assessment.band == "high_risk"
            and (pack.nafdac_number or pack.barcode)
            and (image_bytes or CASE_WORDS.search(message or ""))):
        proposal = _proposal(pack, assessment)
    return {"answer": text, "proposal": proposal,
            "extraction": extraction.to_dict() if extraction else None,
            "assessment": assessment.to_dict(), "provider": who, "steps": 1}


def _remember_turn(case_id: str, message: str,
                   image_bytes: bytes | None, out: dict) -> None:
    """File one exchange under its case. Silent when memory is off."""
    if not case_id:
        return
    recall.remember(case_id, "user",
                    message or ("(photo only)" if image_bytes else ""))
    recall.remember(case_id, "assistant", (out or {}).get("answer", ""))


def _napams_answer(message: str, pack: PackInput,
                   history=None) -> str | None:
    """Read a pasted NAPAMS portal reply. None when this isn't one."""
    text = (message or "").strip()
    low = text.lower()
    if len(text) < 25:
        return None
    if "napams" not in low and "registration.nafdac" not in low \
            and "certificate number" not in low \
            and not ("product details" in low and "nafdac no" in low):
        return None
    res = napams.read_reply(text, pack.nafdac_number or "")
    if res["verdict"] == napams.UNCLEAR and not pack.nafdac_number \
            and not res["about"]:
        return None
    num = res["about"] or pack.nafdac_number or "this number"
    quote = f"\n\n“{res['quote']}”" if res["quote"] else ""
    said_before = any((h.get("role") == "assistant")
                      for h in (history or []))
    bridge = (" This replaces my earlier “no record” answer — that "
              "search only covered the medicines register."
              if res.get("matches") and said_before else "")
    det = res.get("details") or {}
    if res["verdict"] == napams.WRONG_NUMBER:
        return (f"That reply is about {res['about']}, but we were "
                f"checking {pack.nafdac_number}. Confirm which number "
                f"you pasted.{quote}")
    if det:
        napams.file_details(res)
        head = f"NAPAMS lists {num} as {det.get('product', 'a product')}"
        if det.get("manufacturer"):
            head += f" by {det['manufacturer']}"
        bits = [b for b in (det.get("category"),
                            f"approved {det['approved']}"
                            if det.get("approved") else "",
                            f"registration ends {det['expiry']}"
                            if det.get("expiry") else "") if b]
        if bits:
            head += ". " + "; ".join(bits)
        head += "."
        if res["verdict"] == napams.EXPIRED:
            return (head + bridge + " Its certificate has expired, "
                    "though — stock may predate the lapse, but it is "
                    "worth asking the seller about." + quote)
        return (head + bridge + " That is the portal's statement "
                "about the number — a record match is not proof of "
                "the pack in your hand." + quote)
    if res["verdict"] == napams.REGISTERED:
        return (f"NAPAMS says {num} is registered. That is the portal's "
                f"statement about the number — a record match is not "
                f"proof of the pack in your hand.{quote}")
    if res["verdict"] == napams.NOT_FOUND:
        return (f"NAPAMS says it has no record of {num}. Do not use it. "
                f"Keep the pack and receipt.{quote}")
    return ("I couldn't tell from that paste whether NAPAMS confirmed "
            "the number. Paste the portal's reply word-for-word — the "
            "part naming the number and its status.")


def _napams_note(assessment: dict) -> str:
    """Point at NAPAMS when the medicines register drew a blank.

    Only when the kind is food-like or entirely unknown: a medicine with
    a name and maker behind it keeps its finding without the aside.
    """
    checks = (assessment or {}).get("checks", []) or []
    if not any(c.get("id") == "registry"
               and (c.get("evidence") or "").startswith(
                   ("No registry record", "No Greenbook record"))
               for c in checks):
        return ""
    kind = (assessment or {}).get("kind", "")
    inp = (assessment or {}).get("input", {}) or {}
    known = bool(inp.get("product_name") or inp.get("manufacturer")
                 or inp.get("barcode"))
    num = inp.get("nafdac_number") or ""
    hit = napams.known(num) if num else None
    if hit:
        seen = (f"seen in {hit['hits']} pastes" if hit.get("hits", 0) != 1
                else "seen in 1 paste")
        line = (f"Someone checked this number on NAFDAC's NAPAMS portal "
                f"on {hit['first_seen']}: {hit['product']}"
                + (f" by {hit['maker']}" if hit.get("maker") else "")
                + (f", registration ends {hit['expiry']}"
                   if hit.get("expiry") else "")
                + f" ({seen}).")
        return ("\n\n" + line + " That shared reply — not a live lookup — "
                "is the authority here; confirm on NAPAMS for anything "
                "important.")
    if kind in ("food", "beverage", "water", "cosmetic") or not known:
        return ("\n\nIf this is food, water, a drink or cosmetics, check the "
                "number on NAFDAC's NAPAMS portal "
                "(registration.nafdac.gov.ng) — the medicines register I "
                "searched does not list those products. Paste what the "
                "portal says here and I'll read it for you.")
    return ""


def _noted(case_id: str, message: str,
           image_bytes: bytes | None, out: dict) -> dict:
    """Attach the NAPAMS pointer, file the exchange, return it."""
    out["answer"] = (out.get("answer") or "") + _napams_note(
        out.get("assessment"))
    _remember_turn(case_id, message, image_bytes, out)
    return out


def chat(message: str, history=None, image_bytes: bytes | None = None,
         image_type: str = "image/jpeg", case_id: str = "") -> dict:
    """One chat turn. Planned with a key, deterministic without.

    Simple turns answer instantly even with a key: anything the
    deterministic layer settles never reaches the model. A case id
    attaches cross-session memory: recalled turns join this session's
    history, oldest first, and the exchange is filed back.
    """
    mem_id = case_id if recall.attached(case_id) else ""
    merged = list(history or [])
    if mem_id:
        merged = recall.context(mem_id) + merged
    if API_KEY and not image_bytes:
        fast = _fast_answer(message or "", merged)
        if fast is not None:
            return _noted(mem_id, message or "", image_bytes, fast)
    if API_KEY:
        try:
            out = _plan(message or "", _clean_history(merged),
                        image_bytes, image_type)
            return _noted(mem_id, message or "", image_bytes, out)
        except Exception as exc:
            audit.log("agent_fallback", "", str(exc)[:200])
    return _noted(mem_id, message or "", image_bytes,
                  _pipeline(message or "", image_bytes, image_type, merged))


def execute_confirmed(action: dict, username: str) -> dict:
    """Run a user-confirmed proposal. Fresh verdict, never the client's."""
    if (action or {}).get("action") != "open_case":
        raise ValueError("Unknown action.")
    pack = _pack_fields((action or {}).get("pack") or {})
    assessment = _assess(pack)
    case = cases.create_case(pack, assessment, username=username)
    for doc in actions.all_documents(pack, assessment).values():
        cases.attach_document(case["id"], doc)
    audit.log("case_opened", username, case["id"])
    return {"ok": True, "case_id": case["id"], "band": assessment.band,
            "headline": assessment.headline}
