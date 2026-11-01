"""
Vouch — guided multi-side scanning.

A single photo of the front of a pack is a weak input: the registration number,
batch and expiry are usually on the back, and the barcode is on a third face.
This module takes several frames of the same pack, measures how usable each
frame is, reads every frame, then reconciles the readings into one set of
fields with the disagreements left visible.

The rules from the rest of Vouch still apply:

  * The reader transcribes. It does not decide whether a product is real.
  * A field seen on only one soft frame is not promoted to fact. Confidence is
    kept with the field and conflicts are reported, not averaged away.
  * No reading at all is an honest failure, not an empty result.

Nothing here reaches the network. `vision.extract_from_image` may use a
configured model, and the returned provider name says which reader was used.
"""

from __future__ import annotations

import io
import re
import time
from collections import Counter, defaultdict

import barcode as B
import expiry as E
import vision

TEXT_FIELDS = (
    "nafdac_number", "product_name", "manufacturer", "batch",
    "exp_date", "mfg_date", "strength", "pack_size",
)

# Fields the live camera reader is allowed to contribute. A date is only sent
# when two frames agree on it, so a mis-aimed frame cannot set it alone.
LIVE_FIELDS = ("nafdac_number", "product_name", "batch", "exp_date", "mfg_date")

DATE_FIELDS = {"exp_date", "mfg_date"}
CODE_FIELDS = {"nafdac_number", "batch", "barcode"}

DEFAULT_SIDES = [
    ("front", "Front panel", "Brand and product name"),
    ("back", "Back panel", "Registration, maker, batch and expiry"),
    ("code", "Barcode / QR", "Any barcode or QR code"),
    ("detail", "Seal / damage", "Optional: sticker, seal or print fault"),
]

# A QR code is not automatically a product code. Only accept payloads that are
# themselves a GTIN-length number, or that carry a NAFDAC number in a form the
# rest of the app already understands.
_NAFDAC_IN_TEXT = re.compile(
    r"NAFDAC[^A-Z0-9]{0,12}(?:REG\.?|REGISTRATION)?[^A-Z0-9]{0,8}"
    r"(?:NO\.?|NUMBER)?[^A-Z0-9]{0,8}([A-Z]{0,2}\s?\d{1,2}\s?[-\s]?\s?\d{3,7})",
    re.IGNORECASE,
)
_NAFDAC_BARE = re.compile(
    r"(?<![A-Z0-9])([A-Z]{0,2}\s?\d{1,2}\s?-\s?\d{3,7})(?![\dA-Z])"
)
# A registration number can also be printed with no prefix at all ("01-2345").
# That shape collides with dates, so it is reported as a weak candidate and is
# never used to lock a frame on its own.
_LIVE_BARE = re.compile(r"(?<![\dA-Z])(\d{1,2}\s?-\s?\d{3,7})(?![\dA-Z])")
_LABEL_WORDS = {"NO", "NR", "BN", "REG", "LOT", "EXP", "MFG", "REF", "CODE"}

# Date labels for the live pass. Best-before is kept apart from expiry: on food
# and drink it is a quality limit, while a medicine expiry is a hard limit.
_LIVE_DATE_LABELS = (
    ("best_before", re.compile(r"BEST\s*BEFORE|BBE|USE\s*BY", re.IGNORECASE)),
    ("exp", re.compile(r"EXP(?:IRY|IRES|IRATION)?|EXP\.?\s*DATE", re.IGNORECASE)),
    ("mfg", re.compile(r"MFG|MFD|MANUFACTURED|PROD(?:UCTION)?|P\.?\s*DATE",
                       re.IGNORECASE)),
)


def _nafdac_value(token: str) -> str:
    return re.sub(r"\s+", "", str(token or "")).upper()


def _prep_codes(text: str) -> str:
    """Repair the one OCR confusion that is safe to fix before matching.

    A lowercase ``l`` inside a printed code is almost always a digit one; the
    Windows reader turns "A11-0551" into "All-0551". Only the code-matching
    copy is changed, never the text shown to the user.
    """
    return re.sub(r"[l|]", "1", str(text or ""))


def live_candidates(text: str) -> list[dict]:
    """Pull NAFDAC-shaped tokens out of live OCR.

    A token is *strong* when the label says NAFDAC next to it, or when it has
    the letter prefix used by drugs and devices (A11-0551). A bare "01-2345"
    is weak: it can be a registration number or a date, so the UI asks for a
    second look or a registry match before it is trusted.
    """
    raw = _prep_codes(text or "")
    found: dict[str, dict] = {}

    def add(token: str, strength: str, source: str) -> None:
        value = _nafdac_value(token)
        if not re.fullmatch(r"[A-Z0-9]{0,2}\d{1,2}-\d{3,7}", value):
            return
        prefix = re.match(r"[A-Z]{0,2}", value).group(0)
        if prefix in _LABEL_WORDS:
            return
        if not 5 <= len(re.sub(r"[^0-9]", "", value)) <= 9:
            return
        if strength == "strong" and source != "labelled" and not re.search(r"[A-Z]", value):
            strength = "weak"
        current = found.get(value)
        if current is None:
            found[value] = {"value": value, "strength": strength, "source": source}
        elif strength == "strong":
            current["strength"] = "strong"
            current["source"] = source

    for match in _NAFDAC_IN_TEXT.finditer(raw):
        add(match.group(1), "strong", "labelled")
    for match in _NAFDAC_BARE.finditer(raw):
        add(match.group(1), "strong", "printed")
    for match in _LIVE_BARE.finditer(raw):
        add(match.group(1), "weak", "number-like")

    order = {"strong": 0, "weak": 1}
    return sorted(found.values(), key=lambda c: order.get(c["strength"], 2))[:4]


# Scratch-to-verify panels (NAFDAC MAS and maker schemes): a hidden PIN under
# a scratch strip, texted to a shortcode. The PIN is the product's own
# authentication code, so reading it exactly matters more than reading fast.
_PIN_LABEL = re.compile(
    r"SCRATCH|PIN\b|CODE|SMS|TEXT|VERIFY|AUTHENTIC|REVEAL", re.IGNORECASE)
_PIN_RUN = re.compile(r"(?<![A-Z0-9])([A-Z0-9]{6,16})(?![A-Z0-9])")
_SHORTCODE_RES = (
    re.compile(r"(?:SMS|TEXT|SEND)[^0-9]{0,20}(\d{3,6})", re.IGNORECASE),
    re.compile(r"SHORT\s?CODE[^0-9]{0,8}(\d{3,6})", re.IGNORECASE),
)
# Printed barcodes share PIN lengths, so bare digit runs of barcode length are
# never PINs on their own.
_BARCODE_LENS = {8, 12, 13, 14}


def _pin_labelled(text: str, at: int) -> bool:
    window = (text or "")[max(0, at - 28):at + 28]
    return bool(_PIN_LABEL.search(window))


def live_pins(text: str) -> list[dict]:
    """Pull scratch-PIN candidates out of OCR text.

    A run counts when a scratch/PIN/SMS label sits next to it, or when it is
    a long digit-heavy run no barcode or batch code could be. Strength says
    which: strong reads can lock a frame, weak ones only corroborate.
    """
    raw = str(text or "").upper()
    found: dict[str, dict] = {}

    def add(value: str, strength: str) -> None:
        value = re.sub(r"\s+", "", value).upper()
        if "-" in value or len(value) < 6:
            return
        digits = sum(ch.isdigit() for ch in value)
        if strength == "strong" and digits < 2:
            strength = "weak"
        current = found.get(value)
        if current is None:
            found[value] = {"value": value, "strength": strength}
        elif strength == "strong":
            current["strength"] = "strong"

    spans: list[tuple[str, int]] = []
    # PINs are printed grouped ("4829 1047 2213", "4829-1047-2213"): join
    # the groups first. Dashes inside one run are PIN separators, so the
    # joined value must never contain one when it reaches add().
    for match in re.finditer(r"\b(\d{3,4}(?:[\s-]+\d{3,4}){1,3})\b", raw):
        token = re.sub(r"[\s-]+", "", match.group(1))
        if 6 <= len(token) <= 16:
            spans.append((token, match.start(1)))
    for match in _PIN_RUN.finditer(raw):
        spans.append((match.group(1), match.start(1)))
    for token, at in spans:
        digits = sum(ch.isdigit() for ch in token)
        if _pin_labelled(raw, at):
            if digits >= 2:
                add(token, "strong")
        elif (len(token) >= 10 and digits >= 4
                and not (token.isdigit() and len(token) in _BARCODE_LENS)):
            add(token, "weak")

    order = {"strong": 0, "weak": 1}
    return sorted(found.values(),
                  key=lambda c: order.get(c["strength"], 2))[:3]


def live_shortcode(text: str) -> str:
    """The number to text the PIN to, when the label states one."""
    for pattern in _SHORTCODE_RES:
        match = pattern.search(text or "")
        if match:
            return match.group(1)
    return ""


def live_dates(text: str) -> list[dict]:
    """Pull labelled dates out of live OCR: best-before, expiry, manufacture.

    A date only counts when its label is printed next to it, so a stray batch
    digit can never become an expiry. Each hit carries a `month` key
    ("YYYY-MM" or "YYYY") that the camera loop uses to vote across frames.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for kind, labels in _LIVE_DATE_LABELS:
        for match in labels.finditer(text or ""):
            window = (text or "")[match.end():match.end() + 26]
            parsed = E.parse_date(window)
            if parsed is None:
                continue
            month = (f"{parsed.year:04d}-{parsed.month:02d}"
                     if parsed.month else f"{parsed.year:04d}")
            key = kind + ":" + month
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "kind": kind,
                "label": match.group(0).strip().upper(),
                "text": parsed.raw,
                "month": month,
            })
            break
    return out[:3]


def live_read(data: bytes) -> dict:
    """Read one frame from the live camera.

    This is the fast path behind the scanning reticle: it only transcribes, so
    the same rules as the rest of Vouch apply. It never decides that a product
    is real or fake, and a failure to read is reported as a failure.
    """
    started = time.time()
    try:
        text = vision.windows_ocr(data, timeout=25)
    except vision.OcrUnavailable as exc:
        raise vision.VisionUnavailable(
            f"No on-device text reader is available ({exc})."
        ) from exc

    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    plain = "\n".join(lines)
    fields: dict[str, str] = {}
    candidates = live_candidates(plain)
    dates = live_dates(plain)
    pins = live_pins(plain)
    shortcode = live_shortcode(plain)
    if plain:
        try:
            ex = vision.parse_label_text(plain)
        except vision.VisionUnavailable:
            ex = None
        if ex is not None:
            payload = ex.to_dict()
            for name in TEXT_FIELDS:
                value = payload.get(name)
                if value:
                    fields[name] = value
    if "nafdac_number" not in fields:
        strong = [c for c in candidates if c["strength"] == "strong"]
        if strong:
            fields["nafdac_number"] = strong[0]["value"]

    return {
        "ok": True,
        "reader": "windows-ocr",
        "ai_used": False,
        "text": plain[:900],
        "nafdac": candidates,
        "dates": dates,
        "pins": pins,
        "shortcode": shortcode,
        "fields": fields,
        "lines_read": len(lines),
        "ms": int((time.time() - started) * 1000),
    }



# --------------------------------------------------------------------------
# Frame quality — a deterministic vision check, not a model guess
# --------------------------------------------------------------------------

def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def frame_quality(data: bytes) -> dict:
    """How likely is this frame to be readable?

    Sharpness is the variance of the Laplacian; lighting and contrast come from
    luminance statistics; glare is the share of near-white, low-detail pixels.
    The numbers are a coach, not a verdict: the OCR result is still the thing
    that matters.
    """
    fallback = {
        "score": 50, "verdict": "unknown", "sharpness": 50, "lighting": 50,
        "contrast": 50, "coverage": 50, "glare": 0,
        "advice": "Quality could not be measured on this machine.",
        "width": 0, "height": 0,
    }
    try:
        import numpy as np
        from PIL import Image, ImageOps
    except Exception:
        return fallback

    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
        width, height = img.size
        img.thumbnail((480, 480))
        arr = np.asarray(img, dtype=np.float32)
        if arr.ndim != 3 or arr.shape[0] < 8 or arr.shape[1] < 8:
            return fallback
        gray = arr.mean(axis=2)

        brightness = float(gray.mean() / 255.0)
        contrast = float(gray.std() / 255.0)

        lap = (-4.0 * gray[1:-1, 1:-1]
               + gray[:-2, 1:-1] + gray[2:, 1:-1]
               + gray[1:-1, :-2] + gray[1:-1, 2:])
        lap_var = float(lap.var())

        gx = np.abs(np.diff(gray, axis=1))
        gy = np.abs(np.diff(gray, axis=0))
        edge = float(((gx[:-1, :] + gy[:, :-1]) > 22).mean())

        near_white = (arr.min(axis=2) > 246)
        # Ignore flat paper background: glare is bright *and* sits on texture.
        textured = (gx[:-1, :] + gy[:, :-1]) > 8
        glare = float((near_white[:-1, :-1] & textured).mean())

        sharp = _clamp((lap_var ** 0.5) / 11.0 * 100.0)
        if brightness < 0.25:
            light = _clamp(brightness / 0.25 * 55.0)
        elif brightness < 0.42:
            light = 55.0 + (brightness - 0.25) / 0.17 * 45.0
        elif brightness <= 0.82:
            light = 100.0
        else:
            light = _clamp(100.0 - (brightness - 0.82) * 160.0, 55.0)
        contrast_score = _clamp(contrast / 0.28 * 100.0)
        coverage = _clamp(edge / 0.07 * 100.0)
        glare_score = _clamp(100.0 - max(0.0, glare - 0.10) * 180.0, 35.0)

        score = (sharp * 0.42 + light * 0.24 + contrast_score * 0.14
                 + coverage * 0.12 + glare_score * 0.08)

        if brightness < 0.22:
            advice = "Too dark. Move the pack towards better light."
        elif brightness > 0.94:
            advice = "Very bright. Tilt the pack away from the lamp."
        elif glare > 0.14:
            advice = "Glare is washing out the print. Tilt the pack slightly."
        elif sharp < 34:
            advice = "The frame is soft. Hold still and let the camera focus."
        elif edge < 0.012:
            advice = "Little text is visible. Fill the frame with the label."
        elif score >= 76:
            advice = "Sharp and evenly lit. Capture this side."
        else:
            advice = "Readable, but a steadier, closer frame will read better."

        verdict = "good" if score >= 72 else "fair" if score >= 48 else "poor"
        return {
            "score": round(score, 1),
            "verdict": verdict,
            "sharpness": round(sharp, 1),
            "lighting": round(light, 1),
            "contrast": round(contrast_score, 1),
            "coverage": round(coverage, 1),
            "glare": round(glare, 3),
            "brightness": round(brightness, 3),
            "advice": advice,
            "width": width,
            "height": height,
        }
    except Exception:
        return fallback


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------

def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _field_key(name: str, value: str) -> str:
    raw = _collapse(value)
    if not raw:
        return ""
    if name in DATE_FIELDS:
        parsed = E.parse_labelled(raw, E.EXP_LABELS if name == "exp_date"
                                  else E.MFG_LABELS) or E.parse_date(raw)
        if parsed is not None:
            return f"{parsed.year:04d}-{parsed.month:02d}"
    if name in CODE_FIELDS:
        return re.sub(r"[^A-Z0-9]", "", raw.upper())
    return re.sub(r"[^a-z0-9]", "", raw.lower())


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (ca != cb),
            ))
        previous = current
    return previous[-1]


def _merge_text(frames: list[dict]) -> tuple[dict, list[dict]]:
    merged: dict[str, str] = {}
    conflicts: list[dict] = []

    for name in TEXT_FIELDS:
        buckets: dict[str, dict] = {}
        for idx, frame in enumerate(frames):
            ex = frame.get("extraction") or {}
            value = _collapse(ex.get(name, ""))
            if not value:
                continue
            key = _field_key(name, value)
            if not key:
                continue
            bucket = buckets.setdefault(key, {
                "value": value, "count": 0, "confidence": 0.0, "sides": [],
            })
            bucket["count"] += 1
            bucket["confidence"] = max(
                bucket["confidence"],
                float(ex.get("confidence") or 0.0),
            )
            bucket["sides"].append(frame.get("label") or f"Side {idx + 1}")

        if not buckets:
            continue
        ranked = sorted(
            buckets.values(),
            key=lambda b: (b["count"], b["confidence"], len(b["value"])),
            reverse=True,
        )
        merged[name] = ranked[0]["value"]
        if len(ranked) > 1:
            conflicts.append({
                "field": name,
                "chosen": ranked[0]["value"],
                "values": [
                    {"value": b["value"], "sides": b["sides"],
                     "count": b["count"]}
                    for b in ranked
                ],
            })

    return merged, conflicts


def _qr_candidates(payloads: list[str]) -> tuple[list[str], str]:
    """Pull useful identifiers out of QR payloads without trusting the rest."""
    codes: list[str] = []
    notes: list[str] = []
    for payload in payloads or []:
        text = _collapse(payload)
        if not text:
            continue
        notes.append(text[:240])
        digits = re.sub(r"\D", "", text)
        if text.isdigit() and len(digits) in (8, 12, 13, 14):
            codes.append(digits)
            continue
        for match in _NAFDAC_IN_TEXT.finditer(text):
            codes.append(re.sub(r"\s+", "", match.group(1)).upper())
        if not _NAFDAC_IN_TEXT.search(text):
            for match in _NAFDAC_BARE.finditer(text):
                codes.append(re.sub(r"\s+", "", match.group(1)).upper())
        # A URL often carries the code as the last path segment or a query.
        if "://" in text:
            tail = re.split(r"[?#/]", text.rstrip("/"))[-1]
            if tail.isdigit() and len(tail) in (8, 12, 13, 14):
                codes.append(tail)
    return codes, "\n".join(notes)


def _resolve_barcodes(frames: list[dict], qr_payloads: list[str],
                      merged: dict) -> dict | None:
    candidates: list[dict] = []
    for idx, frame in enumerate(frames):
        det = frame.get("barcode") or {}
        value = re.sub(r"\D", "", str(det.get("value") or ""))
        if len(value) in (8, 12, 13, 14):
            candidates.append({
                "code": value,
                "format": det.get("format") or "barcode",
                "votes": max(1, int(det.get("votes") or 1)),
                "side": frame.get("label") or f"Side {idx + 1}",
            })
        ex = frame.get("extraction") or {}
        value = re.sub(r"\D", "", str(ex.get("barcode") or ""))
        if len(value) in (8, 12, 13, 14):
            candidates.append({
                "code": value,
                "format": "printed digits",
                "votes": 1,
                "side": frame.get("label") or f"Side {idx + 1}",
            })

    qr_codes, qr_note = _qr_candidates(qr_payloads)
    for code in qr_codes:
        if code.isdigit() and len(code) in (8, 12, 13, 14):
            candidates.append({
                "code": code, "format": "qr code", "votes": 1,
                "side": "QR payload",
            })
        elif re.fullmatch(r"[A-Z]{0,2}\d{1,2}-?\d{3,7}", code):
            merged.setdefault("nafdac_number", code)

    if not candidates:
        return None

    # Fold near-identical reads together. A single misread character should
    # strengthen a code, not split the vote.
    groups: list[dict] = []
    for cand in candidates:
        for group in groups:
            if (len(group["code"]) == len(cand["code"])
                    and _levenshtein(group["code"], cand["code"]) <= 1):
                group["reads"].append(cand)
                group["votes"] += cand["votes"]
                if B.checksum_ok(cand["code"]):
                    group["valid_code"] = cand["code"]
                break
        else:
            groups.append({
                "code": cand["code"],
                "votes": cand["votes"],
                "reads": [cand],
                "valid_code": cand["code"] if B.checksum_ok(cand["code"]) else "",
            })

    def rank(group):
        code = group["valid_code"] or group["code"]
        return (1 if B.checksum_ok(code) else 0, group["votes"], len(code))

    groups.sort(key=rank, reverse=True)
    best = groups[0]
    code = best["valid_code"] or best["code"]
    checksum = B.checksum_ok(code)
    formats = Counter(r["format"] for r in best["reads"])
    sources = []
    for r in best["reads"]:
        if r["side"] not in sources:
            sources.append(r["side"])

    alternatives = []
    for group in groups[1:]:
        alt = group["valid_code"] or group["code"]
        if alt != code:
            alternatives.append({
                "code": alt,
                "votes": group["votes"],
                "checksum": B.checksum_ok(alt),
            })

    merged["barcode"] = code
    return {
        "code": code,
        "format": formats.most_common(1)[0][0],
        "votes": best["votes"],
        "checksum": checksum,
        "consensus": best["votes"] >= 2 or checksum is True,
        "sources": sources,
        "alternatives": alternatives[:4],
        "qr_payload": qr_note,
    }


def _coach(frames: list[dict], merged: dict, barcode: dict | None) -> str:
    missing = []
    if not merged.get("nafdac_number") and not merged.get("barcode"):
        missing.append("the registration number or a barcode")
    if not merged.get("exp_date"):
        missing.append("the expiry date")
    if not merged.get("batch"):
        missing.append("the batch number")

    weak = [f.get("label") for f in frames
            if (f.get("quality") or {}).get("verdict") == "poor"]
    if missing:
        text = "Still missing " + ", ".join(missing) + "."
        if weak:
            text += " Retake " + ", ".join(weak) + " in better light."
        else:
            text += " Try the back panel again, closer and straight on."
        return text
    if barcode and barcode.get("checksum") is False:
        return ("The barcode check digit does not add up. Re-read the digits "
                "directly under the bars before relying on it.")
    if weak:
        return "Read, but " + ", ".join(weak) + " was soft. Check those fields against the pack."
    return "All sides read cleanly. The fields are in the form for you to confirm."


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def analyze(frames: list[dict]) -> dict:
    """Read and reconcile a list of frames.

    `frames` items: {label, data: bytes, media_type, barcode: {...}|None,
    qr: [payloads], live: {...}}. Returns per-side evidence and one merged
    reading.
    """
    sides: list[dict] = []
    qr_payloads: list[str] = []
    providers: list[str] = []
    warnings: list[str] = []
    live_conflicts: list[dict] = []
    live_nafdac: list[dict] = []

    for idx, frame in enumerate(frames):
        label = _collapse(frame.get("label")) or f"Side {idx + 1}"
        data = frame.get("data") or b""
        quality = frame_quality(data)
        barcode = frame.get("barcode") or None
        qr = [str(x) for x in (frame.get("qr") or []) if str(x).strip()]
        qr_payloads.extend(qr)

        hint = frame.get("live") or {}
        hint_fields = {
            name: _collapse((hint.get("fields") or {}).get(name, ""))
            for name in LIVE_FIELDS
        }
        hint_fields = {k: v for k, v in hint_fields.items() if v}
        hint_number = _nafdac_value(hint.get("nafdac") or "")
        if hint_number and not hint_fields.get("nafdac_number"):
            hint_fields["nafdac_number"] = hint_number
        if hint_number:
            live_nafdac.append({
                "value": hint_number,
                "strength": str(hint.get("strength") or "weak"),
                "side": label,
            })

        extraction = None
        error = ""
        try:
            ex = vision.extract_from_image(
                data, frame.get("media_type") or "image/jpeg"
            )
            extraction = ex.to_dict()
            if ex.provider and ex.provider not in providers:
                providers.append(ex.provider)
            for warning in ex.warnings:
                if warning and warning not in warnings:
                    warnings.append(warning)
        except Exception as exc:
            error = str(exc)
            warnings.append(f"{label}: {error}")

        if hint_fields:
            if extraction is None:
                # The live text reader saw this side even though the still frame
                # could not be read. Keep it, but mark it as a weaker read.
                extraction = {name: "" for name in TEXT_FIELDS}
                extraction.update({
                    "confidence": 0.42,
                    "provider": "live-text",
                    "warnings": ["Read from the live camera, not the saved frame."],
                })
                if "live-text" not in providers:
                    providers.append("live-text")
            for name, value in hint_fields.items():
                still = _collapse(extraction.get(name, ""))
                if not still:
                    extraction[name] = value
                elif _field_key(name, still) != _field_key(name, value):
                    live_conflicts.append({
                        "field": name,
                        "still": still,
                        "live": value,
                        "side": label,
                    })

        sides.append({
            "index": idx,
            "label": label,
            "quality": quality,
            "provider": (extraction or {}).get("provider", ""),
            "confidence": (extraction or {}).get("confidence", 0),
            "extraction": extraction,
            "barcode": barcode,
            "qr": qr,
            "live": {
                "nafdac": hint_number,
                "strength": str(hint.get("strength") or ""),
                "fields": hint_fields,
                "verdict": str(hint.get("verdict") or ""),
            },
            "error": error,
        })

    merged, conflicts = _merge_text(sides)
    barcode = _resolve_barcodes(sides, qr_payloads, merged)

    # Scratch PINs vote across sides like any other field: the value seen
    # most (strong reads first) wins, and rivals stay in the conflicts.
    pin_votes: dict[str, dict] = {}
    shortcodes: Counter = Counter()
    for idx, side in enumerate(sides):
        raw = (side.get("extraction") or {}).get("raw_text", "")
        label = side.get("label") or f"Side {idx + 1}"
        for pin in live_pins(raw):
            entry = pin_votes.setdefault(pin["value"], {
                "value": pin["value"], "count": 0, "strength": "weak",
                "sides": [],
            })
            entry["count"] += 1
            if pin["strength"] == "strong":
                entry["strength"] = "strong"
            if label not in entry["sides"]:
                entry["sides"].append(label)
        code = live_shortcode(raw)
        if code:
            shortcodes[code] += 1
    if pin_votes:
        ranked = sorted(pin_votes.values(),
                        key=lambda e: (e["strength"] == "strong",
                                       e["count"], len(e["value"])),
                        reverse=True)
        merged["pin"] = ranked[0]["value"]
        if len(ranked) > 1:
            conflicts.append({
                "field": "pin",
                "chosen": ranked[0]["value"],
                "values": [{"value": e["value"], "sides": e["sides"],
                            "count": e["count"]} for e in ranked],
            })
    if shortcodes:
        merged["shortcode"] = shortcodes.most_common(1)[0][0]

    read = [s for s in sides if s.get("extraction")]
    if not read and not barcode:
        raise vision.VisionUnavailable(
            "No usable text or barcode was found in these frames. "
            + (warnings[0] if warnings else "Try again with more light and less glare.")
        )

    qualities = [s["quality"]["score"] for s in sides
                 if s.get("quality", {}).get("score") is not None]
    overall_quality = round(sum(qualities) / len(qualities), 1) if qualities else 0

    ai_used = any(p.startswith("meta") for p in providers)
    reader = ", ".join(providers) if providers else (
        "camera barcode detection" if barcode else "no reader"
    )

    return {
        "merged": {
            **{name: merged.get(name, "") for name in TEXT_FIELDS},
            "barcode": (barcode or {}).get("code", ""),
            "pin": merged.get("pin", ""),
            "shortcode": merged.get("shortcode", ""),
        },
        "sides": sides,
        "barcode": barcode,
        "conflicts": conflicts,
        "warnings": warnings[:12],
        "coach": _coach(sides, merged, barcode),
        "quality": {
            "overall": overall_quality,
            "verdict": ("good" if overall_quality >= 72 else
                        "fair" if overall_quality >= 48 else "poor"),
        },
        "readers": providers,
        "ai_used": ai_used,
        "frames": len(sides),
        "frames_read": len(read),
        "live": {
            "nafdac": live_nafdac[:6],
            "conflicts": live_conflicts[:6],
        },
        "limits": [
            "The vision reader transcribes; it does not decide whether a pack is real.",
            "A frame read on one side only is reported with lower confidence, not promoted to fact.",
            "A barcode is an identifier and is copyable. A match is not proof of authenticity.",
            "Text read from the live camera is marked separately from the saved frame it came from.",
        ],
    }
