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
from collections import Counter, defaultdict

import barcode as B
import expiry as E
import vision

TEXT_FIELDS = (
    "nafdac_number", "product_name", "manufacturer", "batch",
    "exp_date", "mfg_date", "strength", "pack_size",
)

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
_NAFDAC_BARE = re.compile(r"\b([A-Z]{1,2}\s?\d{1,2}\s?-\s?\d{3,7})\b")


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
    qr: [payloads]}. Returns per-side evidence and one merged reading.
    """
    sides: list[dict] = []
    qr_payloads: list[str] = []
    providers: list[str] = []
    warnings: list[str] = []

    for idx, frame in enumerate(frames):
        label = _collapse(frame.get("label")) or f"Side {idx + 1}"
        data = frame.get("data") or b""
        quality = frame_quality(data)
        barcode = frame.get("barcode") or None
        qr = [str(x) for x in (frame.get("qr") or []) if str(x).strip()]
        qr_payloads.extend(qr)

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

        sides.append({
            "index": idx,
            "label": label,
            "quality": quality,
            "provider": (extraction or {}).get("provider", ""),
            "confidence": (extraction or {}).get("confidence", 0),
            "extraction": extraction,
            "barcode": barcode,
            "qr": qr,
            "error": error,
        })

    merged, conflicts = _merge_text(sides)
    barcode = _resolve_barcodes(sides, qr_payloads, merged)

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
        "limits": [
            "The vision reader transcribes; it does not decide whether a pack is real.",
            "A frame read on one side only is reported with lower confidence, not promoted to fact.",
            "A barcode is an identifier and is copyable. A match is not proof of authenticity.",
        ],
    }
