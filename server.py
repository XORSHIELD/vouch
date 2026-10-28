"""
Vouch — API server.

Run:  python server.py
Then: http://127.0.0.1:8077

Endpoints are deliberately small. The UI does presentation; the engine does
judgement; this file only moves data between them.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, PlainTextResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field as PField

import json

import actions
import ai
import barcode as B
import cases
import engine
import registry as R
import vision
from engine import PackInput

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "ui")

app = FastAPI(title="Vouch", version="0.4.0",
              description="Check it. Chase it.")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

class PackBody(BaseModel):
    nafdac_number: str = ""
    barcode: str = ""
    manufacturer: str = ""
    product_name: str = ""
    batch: str = ""
    mfg_date: str = ""
    exp_date: str = ""
    pack_size: str = ""
    strength: str = ""
    flags: list[str] = PField(default_factory=list)
    seller: str = ""
    location: str = ""
    price: str = ""
    purchased_on: str = ""

    def to_pack(self) -> PackInput:
        return PackInput(**self.model_dump())


class TextBody(BaseModel):
    text: str


class DemoBody(BaseModel):
    key: str
    create_case: bool = False


class CloseBody(BaseModel):
    outcome: str
    status: str = "closed"


class EventBody(BaseModel):
    kind: str = "note"
    note: str = ""


class ExplainBody(PackBody):
    question: str = ""


class BarcodeBody(BaseModel):
    barcode: str
    product_name: str = ""
    manufacturer: str = ""
    batch: str = ""
    exp_date: str = ""
    mfg_date: str = ""
    offline: bool = False


class BarcodeDemoBody(BaseModel):
    key: str
    create_case: bool = False


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "name": "Vouch",
        "version": app.version,
        "today": date.today().isoformat(),
        "vision": vision.capabilities(),
        "ai": ai.status(),
        "barcode": B.capabilities(),
        "rails": {
            "nafdac": "medicines, devices, vaccines, herbals, veterinary",
            "barcode": "food, drink, water, cosmetics and household products",
        },
        "registry_total": _safe_total(),
        "stats": cases.stats(),
    }


def _safe_total() -> int | None:
    try:
        return R.search("", length=1)["recordsTotal"]
    except Exception:
        return None


@app.post("/api/check")
def check(body: PackBody) -> dict:
    pack = body.to_pack()
    assessment = engine.assess(
        pack, seen_batches=cases.seen_batches_for(pack.nafdac_number)
    )
    return assessment.to_dict()


@app.post("/api/extract/text")
def extract_text(body: TextBody) -> dict:
    try:
        ex = vision.parse_label_text(body.text)
    except vision.VisionUnavailable as exc:
        raise HTTPException(400, str(exc))
    return {"extraction": ex.to_dict()}


@app.post("/api/extract/image")
def extract_image(file: UploadFile = File(...)) -> dict:
    # Sync on purpose: OCR needs its own event loop, and FastAPI runs sync
    # endpoints in a worker thread.
    data = file.file.read()
    if not data:
        raise HTTPException(400, "Empty file.")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Image is larger than 25 MB.")
    try:
        ex = vision.extract_from_image(data, file.content_type or "image/jpeg")
    except vision.VisionUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"extraction": ex.to_dict()}


# --------------------------------------------------------------------------
# Product databases — the second rail
# --------------------------------------------------------------------------

@app.post("/api/barcode")
def barcode_lookup(body: BarcodeBody) -> dict:
    """Resolve a barcode and say what it does and does not establish."""
    code = B.digits_only(body.barcode)
    if not code:
        raise HTTPException(400, "No barcode given.")
    if len(code) not in (8, 12, 13, 14):
        raise HTTPException(
            400, "A barcode is 8, 12, 13 or 14 digits. "
                 f"Got {len(code)}."
        )
    pack = PackInput(
        barcode=code,
        product_name=body.product_name,
        manufacturer=body.manufacturer,
        batch=body.batch,
        exp_date=body.exp_date,
        mfg_date=body.mfg_date,
    )
    assessment = engine.assess(pack, offline=body.offline)
    return {
        "barcode": assessment.barcode,
        "kind": assessment.kind,
        "rail": assessment.rail,
        "assessment": assessment.to_dict(),
    }


@app.get("/api/barcodes")
def barcode_demos() -> dict:
    return {"demos": B.demos(), "capabilities": B.capabilities()}


@app.post("/api/demo/barcode")
def demo_barcode(body: BarcodeDemoBody) -> dict:
    demo = next((d for d in B.demos() if d.get("key") == body.key), None)
    if demo is None:
        raise HTTPException(404, f"No barcode demo '{body.key}'.")
    p = demo.get("pack") or {}
    pack = PackInput(
        barcode=p.get("barcode", ""),
        product_name=p.get("product_name", ""),
        manufacturer=p.get("manufacturer", ""),
        batch=p.get("batch", ""),
        exp_date=p.get("exp_date", ""),
        mfg_date=p.get("mfg_date", ""),
    )
    assessment = engine.assess(pack)
    out = {"assessment": assessment.to_dict(), "demo": demo, "pack": p}
    if body.create_case:
        case = cases.create_case(pack, assessment)
        for doc in actions.all_documents(pack, assessment).values():
            cases.attach_document(case["id"], doc)
        out["case_id"] = case["id"]
        out["case"] = cases.get_case(case["id"])
    return out


# --------------------------------------------------------------------------
# Explanation — streamed, grounded, guarded
# --------------------------------------------------------------------------

@app.get("/api/ai/status")
def ai_status() -> dict:
    return ai.status()


@app.post("/api/explain")
def explain_stream(body: ExplainBody) -> StreamingResponse:
    """The explanation, in real time. Server-sent events, so it starts
    arriving before the last sentence exists."""
    pack = body.to_pack()
    assessment = engine.assess(
        pack, seen_batches=cases.seen_batches_for(pack.nafdac_number)
    )

    def gen():
        for event in ai.stream(assessment, body.question):
            yield "data: " + json.dumps(event) + "\n\n"
        yield "data: " + json.dumps({"end": True}) + "\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


@app.post("/api/ask")
def ask(body: ExplainBody) -> dict:
    pack = body.to_pack()
    assessment = engine.assess(
        pack, seen_batches=cases.seen_batches_for(pack.nafdac_number)
    )
    text, who = ai.compose(assessment, body.question)
    return {
        "answer": text,
        "provider": who,
        "band": assessment.band,
        "kind": assessment.kind,
        "rail": assessment.rail,
        "guardrails": ai.violations(text, assessment),
    }


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------

@app.post("/api/cases")
def create_case(body: PackBody) -> dict:
    pack = body.to_pack()
    assessment = engine.assess(
        pack, seen_batches=cases.seen_batches_for(pack.nafdac_number)
    )
    case = cases.create_case(pack, assessment)
    docs = actions.all_documents(pack, assessment)
    for doc in docs.values():
        cases.attach_document(case["id"], doc)
    return {"case_id": case["id"], "case": cases.get_case(case["id"])}


@app.get("/api/cases")
def list_cases(status: Optional[str] = None, limit: int = 50) -> dict:
    return {"cases": cases.list_cases(limit=limit, status=status),
            "stats": cases.stats()}


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict:
    c = cases.get_case(case_id)
    if not c:
        raise HTTPException(404, "No such case.")
    return c


@app.post("/api/cases/{case_id}/documents/{kind}")
def make_document(case_id: str, kind: str) -> dict:
    c = cases.get_case(case_id)
    if not c:
        raise HTTPException(404, "No such case.")
    a = c.get("assessment") or {}
    payload = a.get("input") or {}
    pack = PackInput(**{k: payload.get(k, "") for k in PackInput().__dict__})
    # Rebuild a lightweight assessment view from the stored payload.
    assessment = _assessment_from_payload(a)
    builder = {
        "nafdac_report": actions.nafdac_report,
        "fccpc_complaint": actions.fccpc_complaint,
        "seller_demand": actions.seller_demand,
    }.get(kind)
    if builder is None:
        raise HTTPException(400, f"Unknown document kind '{kind}'.")
    doc = builder(pack, assessment)
    cases.attach_document(case_id, doc)
    return doc.to_dict()


@app.get("/api/cases/{case_id}/documents/{kind}", response_class=PlainTextResponse)
def download_document(case_id: str, kind: str) -> str:
    c = cases.get_case(case_id)
    if not c:
        raise HTTPException(404, "No such case.")
    a = c.get("assessment") or {}
    payload = a.get("input") or {}
    pack = PackInput(**{k: payload.get(k, "") for k in PackInput().__dict__})
    assessment = _assessment_from_payload(a)
    builder = {
        "nafdac_report": actions.nafdac_report,
        "fccpc_complaint": actions.fccpc_complaint,
        "seller_demand": actions.seller_demand,
    }.get(kind)
    if builder is None:
        raise HTTPException(400, f"Unknown document kind '{kind}'.")
    doc = builder(pack, assessment)
    return doc.body


@app.post("/api/cases/{case_id}/close")
def close_case(case_id: str, body: CloseBody) -> dict:
    c = cases.close_case(case_id, body.outcome, body.status)
    if not c:
        raise HTTPException(404, "No such case.")
    return c


@app.post("/api/cases/{case_id}/event")
def add_event(case_id: str, body: EventBody) -> dict:
    if not cases.get_case(case_id):
        raise HTTPException(404, "No such case.")
    cases.add_event(case_id, body.kind, body.note)
    return cases.get_case(case_id)


@app.get("/api/cluster")
def get_cluster(nafdac: str = "", batch: str = "") -> dict:
    return cases.cluster(nafdac, batch)


@app.get("/api/queue")
def get_queue(limit: int = 50) -> dict:
    """What is due now. The follow-through half of the product."""
    return {"queue": cases.queue(limit=limit), "stats": cases.stats()}


@app.post("/api/cases/{case_id}/steps/{day}")
def complete_step(case_id: str, day: int, body: EventBody | None = None) -> dict:
    if not cases.get_case(case_id):
        raise HTTPException(404, "No such case.")
    c = cases.mark_step(case_id, day, (body.note if body else "") or "")
    return c or {}


@app.post("/api/cases/{case_id}/snooze")
def snooze_case(case_id: str, days: int = 3) -> dict:
    if not cases.get_case(case_id):
        raise HTTPException(404, "No such case.")
    return cases.snooze(case_id, days) or {}


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------

@app.get("/api/demo/packs")
def demo_packs() -> dict:
    return {"packs": vision.fixtures()}


@app.post("/api/demo/seed")
def demo_seed() -> dict:
    """Create three cases with different pasts, so the follow-up screen can be
    shown honestly without waiting a week.

    Labels in the UI say these are rehearsals. The dates and states are real
    calculations from the same code path a live case uses.
    """
    from datetime import timedelta

    plan = [
        # key, days ago, steps already completed
        ("expired", 9, [1]),
        ("copied_number", 3, []),
        ("inactive", 0, []),
    ]
    made = []
    for key, age, done_days in plan:
        f = vision.fixture(key)
        if not f:
            continue
        ex = vision.from_fixture(key)
        pack = PackInput(flags=f.get("flags", []), **ex.to_pack_kwargs())
        a = engine.assess(pack)
        c = cases.create_case(pack, a, opened_on=date.today() - timedelta(days=age))
        for doc in actions.all_documents(pack, a).values():
            cases.attach_document(c["id"], doc)
        for day in done_days:
            cases.mark_step(c["id"], day, "done before this demo")
        made.append(c["id"])
    return {"cases": made, "queue": cases.queue(), "stats": cases.stats()}


@app.post("/api/demo/run")
def demo_run(body: DemoBody) -> dict:
    try:
        ex = vision.from_fixture(body.key)
    except vision.VisionUnavailable as exc:
        raise HTTPException(404, str(exc))
    f = vision.fixture(body.key) or {}
    pack = PackInput(flags=f.get("flags", []), **ex.to_pack_kwargs())
    assessment = engine.assess(pack, seen_batches=cases.seen_batches_for(pack.nafdac_number))
    out = {"assessment": assessment.to_dict(), "pack": f}
    if body.create_case:
        case = cases.create_case(pack, assessment)
        for doc in actions.all_documents(pack, assessment).values():
            cases.attach_document(case["id"], doc)
        out["case_id"] = case["id"]
        out["case"] = cases.get_case(case["id"])
    return out


@app.post("/api/demo/reset")
def demo_reset() -> dict:
    cases.reset()
    return {"ok": True, "stats": cases.stats()}


@app.get("/api/stats")
def get_stats() -> dict:
    return cases.stats()


# --------------------------------------------------------------------------
# Static UI
# --------------------------------------------------------------------------

def _assessment_from_payload(a: dict) -> engine.Assessment:
    """Rebuild enough of an Assessment for the document builders."""
    checks = [engine.Check(**c) for c in a.get("checks", [])]
    acts = [engine.Action(**x) for x in a.get("actions", [])]
    return engine.Assessment(
        band=a.get("band", "review"),
        headline=a.get("headline", ""),
        checks=checks,
        actions=acts,
        score=float(a.get("score") or 0),
        registry=a.get("registry"),
        input=a.get("input") or {},
        generated_at=a.get("generated_at", ""),
        kind=a.get("kind", "medicine"),
        rail=a.get("rail", "nafdac"),
        barcode=a.get("barcode"),
    )


@app.get("/")
def index() -> FileResponse:
    # Keep the full app experience at / and expose marketing at /marketing
    return FileResponse(os.path.join(UI, "marketing.html"))


@app.get("/app")
def app_page() -> FileResponse:
    return FileResponse(os.path.join(UI, "index.html"))


app.mount("/samples", StaticFiles(directory=os.path.join(HERE, "samples", "img")),
          name="samples")
app.mount("/assets", StaticFiles(directory=os.path.join(HERE, "assets")),
          name="assets")

if os.path.isdir(UI):
    app.mount("/ui", StaticFiles(directory=UI), name="ui")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8077, log_level="info")
