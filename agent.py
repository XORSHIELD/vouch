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
from dataclasses import asdict, fields

import actions
import ai
import audit
import cases
import engine  # noqa: F401  (re-exported for Assessment/PackInput users)
import manufacturer as M
import mas
import registry as R
import vision
from engine import Assessment, PackInput

API_KEY = os.environ.get("MODEL_API_KEY") or os.environ.get("META_API_KEY") or ""
API_BASE = os.environ.get("META_BASE", "https://api.meta.ai/v1")
MODEL = os.environ.get("VOUCH_MODEL", "muse-spark-1.3")

MAX_STEPS = 6
MAX_HISTORY = 10
MAX_IMAGE_B64 = 12_000_000
TIMEOUT = 45
CASE_WORDS = re.compile(r"open a case|report this|file (a )?report|escalate",
                        re.IGNORECASE)
NUMBER_SCAN = re.compile(r"[A-Z]{0,2}\s?\d{1,2}\s?-?\s?\d{3,7}")
BARCODE_SCAN = re.compile(r"\b\d{8}\b|\b\d{12,14}\b")
_SERIES_SHAPE = re.compile(r"[A-Z]{0,2}\d{1,2}-\d{4}(\d{2})?")


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
        "tried": res.tried,
        "signals": res.signals,
    }


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
               "seller_demand": actions.seller_demand}.get(args.get("kind"))
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


TOOLS = {"check_pack": _tool_check, "lookup_number": _tool_lookup,
         "verify_pin": _tool_pin, "verify_serial": _tool_serial,
         "preview_document": _tool_preview_doc, "ask": _tool_ask}


SYSTEM = """You help verify medicines and goods. You have tools; the engine
behind them owns every verdict. You transcribe, explain, and propose —
you never declare a product real, fake, genuine, safe, or counterfeit
in your own words. Quote the engine's band and checks instead.

Tools (call one per step, strict JSON, no other keys):
{"thought": str, "tool": "check_pack", "args": {"pack": {nafdac_number, barcode, manufacturer, product_name, batch, serial, mfg_date, exp_date, seller, location}}}
{"thought": str, "tool": "lookup_number", "args": {"number": str}}
{"thought": str, "tool": "verify_pin", "args": {"pin": str, "shortcode": str}}
{"thought": str, "tool": "verify_serial", "args": {"manufacturer": str, "serial": str}}
{"thought": str, "tool": "preview_document", "args": {"kind": "nafdac_report | fccpc_complaint | seller_demand"}}
{"thought": str, "tool": "ask", "args": {"question": str}}

Finish with {"answer": str} or, only to open a case the user asked
about or a high-risk pack needs, {"proposal": {"action": "open_case",
"pack": {...}}. The user confirms proposals; you never execute them.
A photo already read for you appears as the first tool note."""


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


def _plan(message: str, history: list[dict],
          image_bytes: bytes | None, image_type: str) -> dict:
    ctx: dict = {"pack": None, "assessment": None, "extraction": None}
    messages = [{"role": "system", "content": SYSTEM}] + history
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
        try:
            act = json.loads(_chat(messages))
        except Exception as exc:
            audit.log("agent_step_failed", "", str(exc)[:200])
            break
        if act.get("answer"):
            return _final(str(act["answer"]), ctx, steps,
                          f"meta:{MODEL}", message)
        proposal = act.get("proposal") or {}
        if proposal.get("action") == "open_case" and proposal.get("pack"):
            return _final("", ctx, steps, f"meta:{MODEL}", message,
                          confirmed_pack=_pack_fields(proposal["pack"]))
        tool = TOOLS.get(act.get("tool") or "")
        if tool is None:
            break
        try:
            result = tool(act.get("args") or {}, ctx)
        except Exception as exc:
            result = {"error": f"{exc}"[:300]}
        messages.append({"role": "assistant",
                         "content": json.dumps(act)[:2000]})
        messages.append({"role": "user",
                         "content": "tool:" + json.dumps(result)[:4000]})
    return _final("", ctx, steps, f"meta:{MODEL}", message)


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
_LABELLED = re.compile(r"nafdac|reg(?:istration)?(?:\s*(?:no|number|#))?",
                       re.IGNORECASE)
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
    r"([A-Z][A-Za-z&']*)")
_BRAND_STOP = {"NAFDAC", "REG", "NUMBER", "BATCH", "BARCODE", "SCAN",
               "HELP", "PLEASE", "HELLO", "DETAILS", "DETAIL", "PACK",
               "OK", "YES", "NO", "FINE", "THANKS"}
_QUESTION_INTENTS = ("VERDICT_INTENT", "SAFETY_INTENT", "EXPIRY_INTENT",
                     "REPORT_INTENT", "NEXT_INTENT")


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
                 "correction": False}
    msg = text or ""
    up = msg.upper()
    squashed = re.sub(r"(?<=\d) (?=\d)", "", msg)
    m = BARCODE_SCAN.search(squashed)
    if m:
        out["barcode"] = re.sub(r"\s+", "", m.group(0))
    labelled = bool(_LABELLED.search(msg))
    codes_only = bool(re.fullmatch(_CODES_ONLY, up)) and bool(
        re.search(r"\d", up))
    for m in NUMBER_SCAN.finditer(up):
        cand = m.group(0).strip()
        before = up[m.start() - 1] if m.start() > 0 else " "
        after = up[m.end()] if m.end() < len(up) else " "
        if before.isalnum() or after.isalnum():
            continue  # a fragment of a longer pack code, not a number
        if not any(_SERIES_SHAPE.fullmatch(f)
                   for f in R.normalise(cand)):
            continue
        if "-" in cand or labelled or codes_only:
            out["nafdac_number"] = cand
            break
    if not out["nafdac_number"] and not out["barcode"]:
        for run in _CODE_RUN.findall(msg):
            letters = re.search(r"[A-Z]", run)
            digits = re.search(r"\d", run)
            if letters and digits and len(run.strip()) >= 4:
                out["batch"] = run.strip().upper()
                break
        else:
            if codes_only:
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
        if (m and m.group(1).upper() not in _BRAND_STOP
                and m.group(1).lower() not in _KIND_WORDS):
            brand = m.group(1).capitalize()
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
    small = ai._small_talk(message or "")
    if small is not None:
        return small
    if _HOWTO_SCAN.search(message or ""):
        return ("To scan: open the Scan tab and point the camera at the "
                "barcode until it reads — or just type the digits here "
                "and I'll take it from there. The barcode alone can't "
                "confirm a pack, so I'll still need the NAFDAC number or "
                "the dates after.")
    if fresh.get("correction"):
        name = pack.product_name or "that"
        denied = fresh.get("denied") or ""
        ack = (f"Got it — {name}, not {denied}. I've updated that."
               if denied else f"Got it — {name}. I've updated that.")
        return f"{ack} {_tail(pack, assessment, history)}"
    if _is_question(message) and (pack.nafdac_number or pack.barcode
                                  or pack.batch or pack.product_name):
        return ai.answer(message, assessment)
    if _CHECK_IT.search(message or "") and not (
            pack.nafdac_number or pack.barcode or pack.batch):
        return ("I don't have anything on this pack yet. Send the NAFDAC "
                "number, barcode, batch or a photo of the back panel and "
                "I'll check it.")
    return None


def _final(answer: str, ctx: dict, steps: int, provider: str,
           message: str, confirmed_pack: PackInput | None = None) -> dict:
    if ctx.get("assessment") is None:
        ctx["pack"] = _pack_from_text(message)
        ctx["assessment"] = _assess(ctx["pack"])
    if not answer:
        answer, provider = ai.compose(ctx["assessment"], message)
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
    if fresh.get("product_name") and not fresh.get("correction"):
        bits.append(f"Noted — {pack.product_name}.")
    if not bits:
        return None
    return " ".join(bits) + f" {_tail(pack, assessment, history)}"


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
        assessment = _assess(pack)
        text, who = ai.compose(assessment, message)
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
    proposal = None
    if (assessment.band == "high_risk"
            and (pack.nafdac_number or pack.barcode)
            and (image_bytes or CASE_WORDS.search(message or ""))):
        proposal = _proposal(pack, assessment)
    return {"answer": text, "proposal": proposal,
            "extraction": extraction.to_dict() if extraction else None,
            "assessment": assessment.to_dict(), "provider": who, "steps": 1}


def chat(message: str, history=None, image_bytes: bytes | None = None,
         image_type: str = "image/jpeg") -> dict:
    """One chat turn. Planned with a key, deterministic without."""
    if API_KEY:
        try:
            return _plan(message or "", _clean_history(history),
                         image_bytes, image_type)
        except Exception as exc:
            audit.log("agent_fallback", "", str(exc)[:200])
    return _pipeline(message or "", image_bytes, image_type, history)


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
