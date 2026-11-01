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
    payload = {"model": MODEL, "temperature": 0,
               "response_format": {"type": "json_object"},
               "messages": messages}
    req = urllib.request.Request(
        f"{API_BASE}/chat/completions", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {API_KEY}"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"]


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
    """A pack carrying any registration number named in free text."""
    for cand in NUMBER_SCAN.findall((message or "").upper()):
        if R.normalise(cand):
            return PackInput(nafdac_number=cand.strip())
    return PackInput()


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


def _pipeline(message: str, image_bytes: bytes | None,
              image_type: str) -> dict:
    extraction = None
    pack = PackInput()
    if image_bytes:
        try:
            extraction = vision.extract_from_image(image_bytes, image_type)
        except vision.VisionUnavailable as exc:
            return {"answer": str(exc) + " Or type what the pack says.",
                    "proposal": None, "extraction": None,
                    "assessment": _assess(pack).to_dict(),
                    "provider": "vouch-composer", "steps": 1}
        pack = _pack_fields(extraction.to_pack_kwargs())
    else:
        pack = _pack_from_text(message)
    assessment = _assess(pack)
    text, who = ai.compose(assessment, message)
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
    return _pipeline(message or "", image_bytes, image_type)


def execute_confirmed(action: dict, username: str) -> dict:
    """Run a user-confirmed proposal. Fresh verdict, never the client's."""
    if (action or {}).get("action") != "open_case":
        raise ValueError("Unknown action.")
    pack = _pack_fields((action or {}).get("pack") or {})
    assessment = _assess(pack)
    case = cases.create_case(pack, assessment)
    for doc in actions.all_documents(pack, assessment).values():
        cases.attach_document(case["id"], doc)
    audit.log("case_opened", username, case["id"])
    return {"ok": True, "case_id": case["id"], "band": assessment.band,
            "headline": assessment.headline}
