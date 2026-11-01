"""
Vouch — API server.

Run:  python server.py
Then: http://127.0.0.1:8077

Endpoints are deliberately small. The UI does presentation; the engine does
judgement; this file only moves data between them.
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import fields
from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Body, BackgroundTasks, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import (FileResponse, PlainTextResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field as PField

import json

import actions
import agent
import audit
import auth
import backup
import ai
import barcode as B
import cases
import manufacturer as M
import mas
import ratelimit
import sms
import engine
import registry as R
import safety
import scan as S
import vision
import whatsapp
from engine import PackInput

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "ui")


def _cors_origins() -> list:
    # Same-origin UI needs no CORS entry at all; cross-origin callers must be
    # named explicitly. The old default ("*") let any website drive the API.
    raw = os.environ.get("VOUCH_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]

app = FastAPI(title="Vouch", version="0.4.0",
              description="Check it. Chase it.")
def _security_headers() -> dict:
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Content-Security-Policy": (
            "default-src 'self'; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'self'; "
            "img-src 'self' data: blob:; media-src 'self' blob:; "
            "connect-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'"),
        "Permissions-Policy": "camera=(self), microphone=(self)",
    }


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        for key, value in _security_headers().items():
            resp.headers[key] = value
        return resp


app.add_middleware(ratelimit.RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware, allow_origins=_cors_origins(), allow_methods=["*"], allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

class PackBody(BaseModel):
    nafdac_number: str = ""
    barcode: str = ""
    manufacturer: str = ""
    product_name: str = ""
    batch: str = ""
    serial: str = ""
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
        # Subclasses add fields (ExplainBody.question); only ever pass the
        # pack's own fields through.
        allowed = {f.name for f in fields(PackInput)}
        data = {k: v for k, v in self.model_dump().items() if k in allowed}
        return PackInput(**data)


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
        "registry_total": _safe_total(), "sync": R.sync_status(),
        "stats": cases.stats(),
    }


def _safe_total() -> int | None:
    try:
        return R.search("", length=1)["recordsTotal"]
    except Exception:
        return None


def _alert_matches(pack: PackInput) -> list[dict]:
    """Exact batch-level matches from the configured alert file, or nothing."""
    try:
        return safety.match_alerts(pack.nafdac_number, pack.batch, pack.barcode)
    except Exception:
        return []


def _assess(pack: PackInput, **kwargs) -> engine.Assessment:
    kwargs.setdefault("alert_matches", _alert_matches(pack))
    return engine.assess(pack, **kwargs)


@app.post("/api/check")
def check(body: PackBody) -> dict:
    pack = body.to_pack()
    assessment = _assess(
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


def _clean_upload(data: bytes) -> bytes:
    """Accept only real JPG/PNG/WebP bytes, and strip camera metadata.

    Magic bytes are checked instead of trusting the filename or the declared
    content type. EXIF (which can carry GPS) is dropped on the way in, so a
    stored evidence photo cannot leak where it was taken.
    """
    head = bytes(data[:12])
    webp = head[:4] == b"RIFF" and bytes(data[8:12]) == b"WEBP"
    if not (head[:3] == b"\xff\xd8\xff"
            or head[:8] == b"\x89PNG\r\n\x1a\n" or webp):
        raise HTTPException(400, "That file is not a JPG, PNG or WebP image.")
    try:
        import io as _io

        from PIL import Image
        img = Image.open(_io.BytesIO(bytes(data)))
        img.load()
        buf = _io.BytesIO()
        img.save(buf, "PNG" if head[1:4] == b"PNG" else "JPEG")
        return buf.getvalue()
    except HTTPException:
        raise
    except Exception:
        return data


@app.post("/api/extract/image")
def extract_image(file: UploadFile = File(...)) -> dict:
    # Sync on purpose: OCR needs its own event loop, and FastAPI runs sync
    # endpoints in a worker thread.
    data = file.file.read()
    if not data:
        raise HTTPException(400, "Empty file.")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Image is larger than 25 MB.")
    data = _clean_upload(data)
    try:
        ex = vision.extract_from_image(data, file.content_type or "image/jpeg")
    except vision.VisionUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"extraction": ex.to_dict()}


@app.post("/api/scan/live")
def scan_live(file: UploadFile = File(...)) -> dict:
    """Fast text read of one live camera crop.

    The browser detects barcodes and QR codes itself; this endpoint is only for
    printed text: the NAFDAC number, labelled dates and scratch PINs. It
    transcribes, nothing more, and says so when no on-device reader exists.
    """
    data = file.file.read()
    if not data:
        raise HTTPException(400, "Empty frame.")
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "That frame is larger than 8 MB.")
    data = _clean_upload(data)
    try:
        return S.live_read(data)
    except vision.VisionUnavailable as exc:
        raise HTTPException(503, str(exc))


def _form_list(raw: str, default: list | None = None) -> list:
    """Form fields carry JSON. Bad JSON must not 500 the scan."""
    try:
        value = json.loads(raw) if raw else []
    except Exception:
        return list(default or [])
    return value if isinstance(value, list) else list(default or [])


@app.post("/api/scan/multi")
def scan_multi(
    files: list[UploadFile] = File(...),
    sides: str = Form("[]"),
    barcodes: str = Form("[]"),
    qr: str = Form("[]"),
    rotations: str = Form("[]"),
    live: str = Form("[]"),
) -> dict:
    """Read several sides of one pack and reconcile what they say.

    The client rotates and crops each frame before upload; this endpoint treats
    every frame as the same physical pack and keeps per-side evidence. It never
    invents a field that no frame contained.
    """
    if not files:
        raise HTTPException(400, "No frames were supplied.")
    if len(files) > 8:
        raise HTTPException(400, "At most eight sides can be read at once.")

    labels = _form_list(sides)
    detections = _form_list(barcodes)
    payloads = _form_list(qr)
    turns = _form_list(rotations)
    hints = _form_list(live)

    frames: list[dict] = []
    total = 0
    for idx, upload in enumerate(files):
        data = upload.file.read()
        total += len(data)
        if not data:
            raise HTTPException(400, f"Frame {idx + 1} is empty.")
        if total > 100 * 1024 * 1024:
            raise HTTPException(413, "The frames together are larger than 100 MB.")
        data = _clean_upload(data)
        label = labels[idx] if idx < len(labels) and isinstance(labels[idx], str) else ""
        det = detections[idx] if idx < len(detections) and isinstance(detections[idx], dict) else None
        qr_for_side = payloads[idx] if idx < len(payloads) and isinstance(payloads[idx], list) else []
        hint_for_side = hints[idx] if idx < len(hints) and isinstance(hints[idx], dict) else {}
        frames.append({
            "label": label,
            "data": data,
            "media_type": upload.content_type or "image/jpeg",
            "barcode": det,
            "qr": qr_for_side,
            "rotation": turns[idx] if idx < len(turns) else 0,
            "live": hint_for_side,
        })

    try:
        out = S.analyze(frames)
    except vision.VisionUnavailable as exc:
        raise HTTPException(503, str(exc))
    out["rotations"] = turns[:len(frames)]
    return out


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
    assessment = _assess(pack, offline=body.offline)
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
def demo_barcode(body: BarcodeDemoBody, request: Request) -> dict:
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
    assessment = _assess(pack)
    out = {"assessment": assessment.to_dict(), "demo": demo, "pack": p}
    if body.create_case:
        opener = None
        try:
            opener = auth.me(auth.bearer_token(request))
        except Exception:
            opener = None
        if not opener:
            raise HTTPException(401, "Sign in to open cases.")
        case = cases.create_case(pack, assessment,
                                 username=opener["username"])
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
    assessment = _assess(
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


class AgentBody(BaseModel):
    message: str = ""
    history: list = PField(default_factory=list)
    image_b64: str = ""
    image_type: str = "image/jpeg"
    confirmed_action: dict | None = None


@app.post("/api/agent/chat")
def agent_chat(body: AgentBody, request: Request) -> dict:
    """Chat with the Vouch agent. Reads are public; confirmed actions
    need sign-in, and execution is code, never model output."""
    if body.confirmed_action is not None:
        user = None
        try:
            user = auth.me(auth.bearer_token(request))
        except Exception:
            user = None
        if not user:
            raise HTTPException(401, "Sign in to open cases.")
        try:
            return agent.execute_confirmed(body.confirmed_action,
                                           user["username"])
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    image_bytes = None
    if body.image_b64:
        try:
            image_bytes = agent.decode_image(body.image_b64)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return agent.chat(body.message, body.history, image_bytes,
                      body.image_type or "image/jpeg")


@app.post("/api/ask")
def ask(body: ExplainBody) -> dict:
    pack = body.to_pack()
    assessment = _assess(
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

class AuthBody(BaseModel):
    username: str = ""
    password: str = ""
    email: str = ""


class RoleBody(BaseModel):
    username: str = ""
    role: str = ""


@app.post("/api/auth/register")
def auth_register(body: AuthBody) -> dict:
    """Create a consumer account, with an optional email for sign-in."""
    try:
        user = auth.register(body.username, body.password, email=body.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"user": user}


@app.post("/api/auth/login")
def auth_login(body: AuthBody) -> dict:
    """Trade a username-or-email plus password for a session token."""
    token = auth.login(body.username, body.password)
    if not token:
        raise HTTPException(401, "Wrong username or password.")
    return {"token": token, "user": auth.me(token)}


@app.post("/api/auth/logout")
def auth_logout(request: Request) -> dict:
    auth.logout(auth.bearer_token(request))
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(user: dict = Depends(auth.require_user)) -> dict:
    return {"user": user}


@app.post("/api/auth/role")
def auth_set_role(body: RoleBody,
                  user: dict = Depends(auth.require_admin)) -> dict:
    """Grant a role. Admins only."""
    if not auth.set_role(body.username, body.role):
        raise HTTPException(400, "Unknown user or role.")
    audit.log("role_changed", user.get("username", ""),
              f"{body.username} -> {body.role}")
    return {"ok": True, "username": (body.username or "").strip().lower(),
            "role": body.role}


@app.get("/api/audit")
def audit_log(limit: int = 50,
              user: dict = Depends(auth.require_admin)) -> dict:
    """Recent audit events. Admins only."""
    return {"events": audit.recent(max(1, min(limit, 500)))}


class BackupRestoreBody(BaseModel):
    run: str = ""


def _backup_root() -> str:
    return (os.environ.get("VOUCH_BACKUP_DIR")
            or os.path.join(HERE, "backups"))


@app.get("/api/backups")
def list_backups(user: dict = Depends(auth.require_admin)) -> dict:
    """Backup runs on this server, newest first. Admins only."""
    root = _backup_root()
    runs = []
    try:
        names = sorted(os.listdir(root), reverse=True)
    except OSError:
        names = []
    for name in names:
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        files = sorted(
            r.replace(os.sep, "/")
            for r in backup.rel_paths(backup.SOURCES)
            if os.path.exists(os.path.join(d, r)))
        runs.append({"name": name, "files": files})
    return {"runs": runs}


@app.post("/api/backup/restore")
def restore_backup(body: BackupRestoreBody,
                   user: dict = Depends(auth.require_admin)) -> dict:
    """Restore a backup run over the live data. Admins only.

    Takes a pre-restore snapshot first. Restore during low traffic and
    restart the server afterwards.
    """
    name = (body.run or "").strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise HTTPException(400, "Unknown backup run.")
    run_dir = os.path.join(_backup_root(), name)
    if not os.path.isdir(run_dir):
        raise HTTPException(404, f"No backup run '{name}'.")
    try:
        out = backup.restore_tree(run_dir)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except OSError as exc:
        raise HTTPException(500, f"Restore failed: {exc}")
    audit.log("backup_restore", user.get("username", ""), name)
    return {"restored": out["restored"], "snapshot": out["snapshot"],
            "restart_required": True}


def _owned_case(case_id: str, user: dict) -> dict:
    """One case, or 404 when it is missing or belongs to someone else."""
    c = cases.get_case(case_id)
    if not c or not cases.can_access(c.get("username", ""),
                                      user.get("username", ""),
                                      user.get("role", "")):
        raise HTTPException(404, "No such case.")
    return c


@app.post("/api/cases")
def create_case(body: PackBody, user: dict = Depends(auth.require_user)) -> dict:
    pack = body.to_pack()
    assessment = _assess(
        pack, seen_batches=cases.seen_batches_for(pack.nafdac_number)
    )
    case = cases.create_case(pack, assessment,
                               username=user.get("username", ""))
    docs = actions.all_documents(pack, assessment)
    audit.log("case_opened", user.get("username", ""), case["id"])
    for doc in docs.values():
        cases.attach_document(case["id"], doc)
    return {"case_id": case["id"], "case": cases.get_case(case["id"])}


@app.get("/api/cases")
def list_cases(status: Optional[str] = None, limit: int = 50, user: dict = Depends(auth.require_user)) -> dict:
    return {"cases": cases.list_cases(limit=limit, status=status,
                                        username=user["username"],
                                        role=user["role"]),
            "stats": cases.stats()}


def _csv_cell(v) -> str:
    s = "" if v is None else str(v)
    if s[:1] in ("=", "+", "-", "@"):
        s = "'" + s  # spreadsheet formula injection
    return s


@app.get("/api/cases/export")
def export_cases(user: dict = Depends(auth.require_user)):
    """Cases as a CSV download. Scoped exactly like the list."""
    cols = ["id", "created_at", "band", "score", "headline", "nafdac",
            "product", "manufacturer", "batch", "seller", "location",
            "price", "exp_date", "status", "outcome", "username"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "created", "band", "score", "headline", "nafdac",
                "product", "manufacturer", "batch", "seller", "location",
                "price", "exp_date", "status", "outcome", "owner"])
    for r in cases.export_rows(username=user["username"], role=user["role"]):
        w.writerow([_csv_cell(r.get(c)) for c in cols])
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="vouch-cases-{date.today().isoformat()}.csv"'},
    )


@app.get("/api/cases/{case_id}")
def get_case(case_id: str, user: dict = Depends(auth.require_user)) -> dict:
    return _owned_case(case_id, user)


@app.post("/api/cases/{case_id}/documents/{kind}")
def make_document(case_id: str, kind: str, user: dict = Depends(auth.require_user)) -> dict:
    c = _owned_case(case_id, user)
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
def download_document(case_id: str, kind: str, user: dict = Depends(auth.require_user)) -> str:
    c = _owned_case(case_id, user)
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
def close_case(case_id: str, body: CloseBody, user: dict = Depends(auth.require_user)) -> dict:
    _owned_case(case_id, user)
    c = cases.close_case(case_id, body.outcome, body.status)
    audit.log("case_closed", user.get("username", ""), case_id)
    if not c:
        raise HTTPException(404, "No such case.")
    return c


@app.post("/api/cases/{case_id}/event")
def add_event(case_id: str, body: EventBody, user: dict = Depends(auth.require_user)) -> dict:
    _owned_case(case_id, user)
    cases.add_event(case_id, body.kind, body.note)
    return cases.get_case(case_id)


@app.get("/api/cluster")
def get_cluster(nafdac: str = "", batch: str = "", user: dict = Depends(auth.require_user)) -> dict:
    return cases.cluster(nafdac, batch, username=user["username"],
                         role=user["role"])


@app.get("/api/queue")
def get_queue(limit: int = 50, user: dict = Depends(auth.require_user)) -> dict:
    """What is due now. The follow-through half of the product."""
    return {"queue": cases.queue(limit=limit, username=user["username"],
                                 role=user["role"]),
            "stats": cases.stats()}


@app.post("/api/cases/{case_id}/steps/{day}")
def complete_step(case_id: str, day: int, body: EventBody | None = None, user: dict = Depends(auth.require_user)) -> dict:
    _owned_case(case_id, user)
    c = cases.mark_step(case_id, day, (body.note if body else "") or "")
    return c or {}


@app.post("/api/cases/{case_id}/snooze")
def snooze_case(case_id: str, days: int = 3, user: dict = Depends(auth.require_user)) -> dict:
    _owned_case(case_id, user)
    return cases.snooze(case_id, days) or {}


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------

@app.get("/api/demo/packs")
def demo_packs() -> dict:
    return {"packs": vision.fixtures()}


@app.post("/api/demo/seed")
def demo_seed(user: dict = Depends(auth.require_admin)) -> dict:
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
        a = _assess(pack)
        c = cases.create_case(pack, a, opened_on=date.today() - timedelta(days=age),
                                username=user["username"])
        for doc in actions.all_documents(pack, a).values():
            cases.attach_document(c["id"], doc)
        for day in done_days:
            cases.mark_step(c["id"], day, "done before this demo")
        made.append(c["id"])
    return {"cases": made, "queue": cases.queue(), "stats": cases.stats()}


@app.post("/api/demo/run")
def demo_run(body: DemoBody, request: Request) -> dict:
    try:
        ex = vision.from_fixture(body.key)
    except vision.VisionUnavailable as exc:
        raise HTTPException(404, str(exc))
    f = vision.fixture(body.key) or {}
    pack = PackInput(flags=f.get("flags", []), **ex.to_pack_kwargs())
    assessment = _assess(pack, seen_batches=cases.seen_batches_for(pack.nafdac_number))
    out = {"assessment": assessment.to_dict(), "pack": f}
    if body.create_case:
        opener = None
        try:
            opener = auth.me(auth.bearer_token(request))
        except Exception:
            opener = None
        if not opener:
            raise HTTPException(401, "Sign in to open cases.")
        case = cases.create_case(pack, assessment,
                                 username=opener["username"])
        for doc in actions.all_documents(pack, assessment).values():
            cases.attach_document(case["id"], doc)
        out["case_id"] = case["id"]
        out["case"] = cases.get_case(case["id"])
    return out


@app.post("/api/demo/reset")
def demo_reset(user: dict = Depends(auth.require_admin)) -> dict:
    cases.reset()
    safety.reset()
    audit.log("demo_reset", user.get("username", ""), "")
    return {"ok": True, "stats": cases.stats()}


@app.get("/api/stats")
def get_stats() -> dict:
    return cases.stats()


# --------------------------------------------------------------------------
# Registry browse — the whole Greenbook, not just one number
# --------------------------------------------------------------------------

@app.get("/api/registry/search")
def registry_search(q: str = "", limit: int = 20, page: int = 1) -> dict:
    """Search the synced NAFDAC register by product name, ingredient or number.

    This is the same public dataset the lookup uses, so every product that can
    be verified by number can also be found by name.
    """
    limit = max(1, min(int(limit or 20), 50))
    page = max(1, int(page or 1))
    start = (page - 1) * limit
    try:
        payload = R.search(q, length=limit, start=start)
    except R.RegistryUnavailable as exc:
        raise HTTPException(
            503,
            "The NAFDAC register could not be reached and this search is not "
            f"cached yet ({exc}). Try the number lookup, or retry shortly.",
        )
    records = []
    for d in payload.get("data") or []:
        try:
            rec = R.Record.from_api(d)
        except Exception:
            continue
        records.append({
            "nafdac": rec.nafdac,
            "product_name": rec.product_name,
            "manufacturer": rec.manufacturer,
            "status": rec.status,
            "category": rec.category,
            "form": rec.form,
            "pack_size": rec.pack_size,
            "registration_expiry": rec.registration_expiry,
        })
    return {
        "query": q,
        "page": page,
        "limit": limit,
        "total": payload.get("recordsTotal") or 0,
        "matched": payload.get("recordsFiltered") or 0,
        "records": records,
        "source": R.BASE, "mirror": R.sync_status(),
        "note": "Registration in the Greenbook confirms a licence record, "
                "not the authenticity of a particular pack.",
    }


class SerialBody(BaseModel):
    manufacturer: str = ""
    serial: str = ""


class MasBody(BaseModel):
    pin: str = ""
    shortcode: str = ""


@app.post("/api/mas/verify")
def mas_verify(body: MasBody) -> dict:
    """Ask the connected PIN service. Honest when none is connected."""
    return mas.verify(body.pin, body.shortcode)


@app.post("/api/manufacturer/verify")
def manufacturer_verify(body: SerialBody) -> dict:
    """Ask a connected manufacturer about one serial. Honest when none."""
    return M.verify(body.manufacturer, body.serial)


class SmsBody(BaseModel):
    sender: str = ""
    text: str = ""
    secret: str = ""


@app.post("/api/sms/inbound")
def sms_inbound(body: SmsBody) -> dict:
    """Provider webhook: 'NAFDAC <number>' in, short answer text out."""
    if not sms.check_secret(body.secret):
        raise HTTPException(403, "Bad provider secret.")
    return {"reply": sms.handle_inbound(body.text, body.sender)}


@app.get("/api/whatsapp/webhook")
def wa_verify(request: Request):
    """Meta verification handshake: echo the challenge on token match."""
    q = request.query_params
    hit = whatsapp.check_verify(q.get("hub.mode", ""),
                                q.get("hub.verify_token", ""),
                                q.get("hub.challenge", ""))
    if not hit:
        raise HTTPException(403, "Bad verify token.")
    return PlainTextResponse(hit)


@app.post("/api/whatsapp/webhook")
async def wa_inbound(request: Request) -> dict:
    """Meta message webhook: check-by-message over WhatsApp."""
    raw = await request.body()
    status, out = whatsapp.handle_post(
        raw, request.headers.get("x-hub-signature-256", ""))
    if status != 200:
        raise HTTPException(status, out.get("detail", "Refused."))
    return out


@app.get("/api/registry/sync/status")
def registry_sync_status() -> dict:
    """How fresh the local Greenbook mirror is."""
    return R.sync_status()


@app.post("/api/registry/sync")
def registry_sync_now(bg: BackgroundTasks, user: dict = Depends(auth.require_admin)) -> dict:
    """Start a background sync of the whole Greenbook listing. Resumable."""
    if R.sync_running():
        return {"started": False, "status": R.sync_status()}
    bg.add_task(R.sync_run)
    audit.log("sync_started", user.get("username", ""), "")
    return {"started": True, "status": R.sync_status()}


@app.get("/api/registry/lookup/{number}")
def registry_lookup(number: str) -> dict:
    """Full evidence for one printed number, including the variants tried."""
    try:
        result = R.lookup(number)
    except R.RegistryUnavailable as exc:
        raise HTTPException(503, f"The NAFDAC register is unreachable: {exc}")
    return result.to_dict()


# --------------------------------------------------------------------------
# Nigeria Product Safety Intelligence
# --------------------------------------------------------------------------

@app.get("/api/safety/summary")
def safety_summary() -> dict:
    """What the shared layer knows, and what it cannot know."""
    return safety.summary()


@app.get("/api/safety/alerts")
def safety_alerts(q: str = "", nafdac: str = "", batch: str = "") -> dict:
    return safety.alerts(query=q, nafdac=nafdac, batch=batch)


@app.get("/api/safety/batch")
def safety_batch(nafdac: str = "", batch: str = "", barcode: str = "") -> dict:
    """Batch watch: configured alerts, local submissions and pattern flags."""
    return safety.batch_watch(nafdac=nafdac, batch=batch, barcode=barcode)


@app.get("/api/safety/reports")
def safety_reports(limit: int = 30, nafdac: str = "", batch: str = "",
                   seller: str = "", q: str = "", barcode: str = "") -> dict:
    reports = safety.list_reports(limit=limit, nafdac=nafdac, batch=batch,
                                  seller=seller, query=q, barcode=barcode)
    return {
        "reports": reports,
        "count": len(reports),
        "note": ("Submissions are shown as received. They are not verified findings "
                 "and Vouch does not publish them as accusations."),
    }


@app.post("/api/safety/reports")
def safety_submit(
    product_name: str = Form(""),
    nafdac_number: str = Form(""),
    batch: str = Form(""),
    barcode: str = Form(""),
    seller: str = Form(""),
    location: str = Form(""),
    issue: str = Form("other"),
    notes: str = Form(""),
    contact: str = Form(""),
    file: UploadFile | None = File(None),
) -> dict:
    """Accept one evidence submission for review. Never a public verdict."""
    photo = None
    media_type = "image/jpeg"
    if file is not None:
        media_type = file.content_type or "image/jpeg"
        if not media_type.startswith("image/"):
            raise HTTPException(400, "Evidence must be an image.")
        photo = file.file.read()
        if not photo:
            photo = None
        elif len(photo) > safety.MAX_PHOTO:
            raise HTTPException(413, "The evidence photo is larger than 8 MB.")
        if photo:
            photo = _clean_upload(photo)
    try:
        return safety.submit({
            "product_name": product_name,
            "nafdac_number": nafdac_number,
            "batch": batch,
            "barcode": barcode,
            "seller": seller,
            "location": location,
            "issue": issue,
            "notes": notes,
            "contact": contact,
        }, photo=photo, media_type=media_type)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/safety/photo/{report_id}")
def safety_photo(report_id: str) -> FileResponse:
    found = safety.photo_path(report_id)
    if not found:
        raise HTTPException(404, "No evidence photo for that submission.")
    path, media_type = found
    return FileResponse(path, media_type=media_type)


class ReviewBody(BaseModel):
    id: str = ""
    approved: bool = False


class AlertBody(BaseModel):
    product_name: str = ""
    nafdac_number: str = ""
    batch: str = ""
    reason: str = ""
    report_ids: list = PField(default_factory=list)


@app.get("/api/alerts")
def published_alerts(q: str = "", nafdac: str = "",
                     batch: str = "") -> dict:
    """Public alerts: official file plus moderated community alerts."""
    return safety.published_alerts(query=q, nafdac=nafdac, batch=batch)


@app.get("/api/safety/review-queue")
def review_queue(user: dict = Depends(auth.require_inspector)) -> dict:
    """Submissions awaiting moderation. Inspectors and admins."""
    return {"reports": safety.review_queue()}


@app.post("/api/safety/review")
def review_report(body: ReviewBody,
                  user: dict = Depends(auth.require_inspector)) -> dict:
    out = safety.review(body.id, body.approved, user["username"])
    if out is None:
        raise HTTPException(404, "No such submission.")
    return {"ok": True, "report": out}


@app.post("/api/alerts")
def publish_alert(body: AlertBody,
                  user: dict = Depends(auth.require_inspector)) -> dict:
    try:
        return safety.publish_alert(body.model_dump(), user["username"])
    except ValueError as exc:
        raise HTTPException(422, str(exc))


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
    return FileResponse(os.path.join(UI, "marketing.html"))


@app.get("/app")
def app_page() -> FileResponse:
    return FileResponse(os.path.join(UI, "index.html"))


# Marketing pages share one document and route on the client, so a shared link
# still resolves on a hard refresh.
_MARKETING_PAGES = (
    "/how-it-works", "/features", "/business", "/resources", "/about",
    "/privacy", "/terms", "/report", "/signin", "/signup", "/forgot",
)


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(os.path.join(UI, "favicon.png"))


@app.get("/{page}")
def marketing_page(page: str) -> FileResponse:
    if "/" + page in _MARKETING_PAGES:
        return FileResponse(os.path.join(UI, "marketing.html"))
    # Unknown paths get the shell too; the router shows the not-found page.
    return FileResponse(os.path.join(UI, "marketing.html"))


app.mount("/samples", StaticFiles(directory=os.path.join(HERE, "samples", "img")),
          name="samples")

if os.path.isdir(UI):
    app.mount("/ui", StaticFiles(directory=UI), name="ui")


def _maybe_auto_sync() -> None:
    """Daily Greenbook refresh when VOUCH_AUTO_SYNC=1. A daemon thread."""
    if os.environ.get("VOUCH_AUTO_SYNC") != "1":
        return
    import threading

    def loop() -> None:
        import time as _time
        while True:
            try:
                if not R.mirror_ready():
                    R.sync_run()
                _time.sleep(24 * 3600)
                R.sync_run()
            except Exception:
                _time.sleep(3600)

    threading.Thread(target=loop, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    auth.ensure_admin()
    _maybe_auto_sync()
    port = int(os.environ.get("VOUCH_PORT", "8077"))
    host = os.environ.get("VOUCH_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port, log_level="info")
