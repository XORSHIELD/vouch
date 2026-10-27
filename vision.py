"""
Vouch — pack extraction.

Three ways to get the fields off a pack, in order of preference:

  1. Meta vision model  (META / MODEL_API_KEY set) — reads the photograph.
  2. Pasted label text  (no key needed) — a deterministic parser. Useful when
     someone can read the pack but the camera will not cooperate.
  3. Fixtures           (demo / offline) — labelled packs for a rehearsal.

The model is asked to TRANSCRIBE, not to judge. All judgement happens in
engine.py, which is testable. Never let a language model decide whether a
medicine is real.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, asdict, field

import expiry as E

API_BASE = os.environ.get("META_BASE", "https://api.meta.ai/v1")
API_KEY = os.environ.get("MODEL_API_KEY") or os.environ.get("META_API_KEY") or ""
MODEL = os.environ.get("META_VISION_MODEL", "muse-spark-1.3")

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class VisionUnavailable(RuntimeError):
    pass


@dataclass
class Extraction:
    nafdac_number: str = ""
    barcode: str = ""
    product_name: str = ""
    manufacturer: str = ""
    batch: str = ""
    exp_date: str = ""
    mfg_date: str = ""
    strength: str = ""
    pack_size: str = ""
    raw_text: str = ""
    confidence: float = 0.0
    provider: str = "manual"
    warnings: list[str] = field(default_factory=list)

    def to_pack_kwargs(self) -> dict:
        return {
            "nafdac_number": self.nafdac_number,
            "barcode": self.barcode,
            "product_name": self.product_name,
            "manufacturer": self.manufacturer,
            "batch": self.batch,
            "exp_date": self.exp_date,
            "mfg_date": self.mfg_date,
            "strength": self.strength,
            "pack_size": self.pack_size,
            "extraction_confidence": self.confidence,
        }

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# 2. Paste-the-label parser (deterministic, no model)
# --------------------------------------------------------------------------

_NAFDAC_RE = re.compile(
    r"NAFDAC[^A-Z0-9]{0,12}(?:REG\.?|REGISTRATION)?[^A-Z0-9]{0,6}"
    r"(?:NO\.?|NUMBER)?[^A-Z0-9]{0,6}([A-Z]{0,2}\s?\d{1,2}\s?[-\s]?\s?\d{3,7})",
    re.IGNORECASE,
)
_BATCH_RE = re.compile(
    r"(?:BATCH|LOT|B\.?\s*NO\.?|BATCH\s*NO\.?)[^A-Z0-9]{0,6}([A-Z0-9][A-Z0-9\-\/]{2,19})",
    re.IGNORECASE,
)
_MFG_RE = re.compile(
    r"(?:MANUFACTURED|MADE|PRODUCED)\s+(?:BY|FOR)[^A-Z0-9]{0,4}([A-Z0-9][A-Z0-9&.,'\- ]{2,60})",
    re.IGNORECASE,
)
_STRENGTH_RE = re.compile(r"\b(\d+(?:\.\d+)?\s*(?:MG|ML|MCG|G|%|IU))\b", re.IGNORECASE)
_PACK_RE = re.compile(
    r"\b(\d{1,3}\s*[X×]\s*\d{1,3}|\d{1,3}\s*(?:TABLETS?|CAPSULES?|SACHETS?|PIECES?|BOTTLES?))\b",
    re.IGNORECASE,
)

# Barcodes. Two shapes: an explicitly labelled code, and a bare run of digits.
# The explicit shape may carry spaces or hyphens; the bare shape may not,
# because "01012027" and "01-01-2027" are dates, not product codes.
_BARCODE_LABELLED = re.compile(
    r"(?:BARCODE|EAN[\s\-]?13|EAN|UPC[\s\-]?A?|GTIN(?:[\s\-]?1[234])?)"
    r"[^0-9]{0,14}((?:\d[\s\-]?){7,13}\d)",
    re.IGNORECASE,
)
_BARCODE_BARE = re.compile(r"(?<!\d)(\d{14}|\d{13}|\d{12}|\d{8})(?!\d)")


def find_barcode(text: str) -> str:
    """Pull the printed product code out of label text.

    A number is only accepted if it has a length a real barcode can have. When
    several candidates exist, one with a passing check digit wins, then a
    labelled one, then the longest. A failing check digit is still returned —
    the engine has something to say about it — but never in preference to a
    code that adds up.
    """
    raw = text or ""
    if not raw:
        return ""
    candidates: list[tuple[str, bool, int]] = []
    for m in _BARCODE_LABELLED.finditer(raw):
        code = re.sub(r"\D", "", m.group(1))
        if len(code) in (8, 12, 13, 14):
            candidates.append((code, True, 2))
    for m in _BARCODE_BARE.finditer(raw):
        code = m.group(1)
        if code not in {c[0] for c in candidates}:
            candidates.append((code, False, 1))
    if not candidates:
        return ""

    try:
        import barcode as B
        ok = B.checksum_ok
    except Exception:                                    # pragma: no cover
        def ok(_c):                                      # type: ignore
            return None

    def rank(item):
        code, labelled, weight = item
        checks = ok(code)
        return (2 if checks else 0, weight, len(code))

    candidates.sort(key=rank, reverse=True)
    return candidates[0][0]


def parse_label_text(text: str) -> Extraction:
    """Deterministic extraction from label text pasted or OCR'd elsewhere."""
    raw = (text or "").strip()
    if not raw:
        raise VisionUnavailable("No label text supplied.")

    warnings: list[str] = []

    m = _NAFDAC_RE.search(raw)
    nafdac = re.sub(r"\s+", "", m.group(1)).upper() if m else ""
    if not nafdac:
        warnings.append("No NAFDAC number found in the text.")

    m = _BATCH_RE.search(raw)
    batch = m.group(1).strip().upper() if m else ""
    if not batch:
        warnings.append("No batch number found in the text.")

    exp = E.parse_labelled(raw, E.EXP_LABELS)
    if exp is None:
        warnings.append("No expiry date found in the text.")

    mfg = E.parse_labelled(raw, E.MFG_LABELS)

    m = _MFG_RE.search(raw)
    manufacturer = m.group(1).strip(" .,-") if m else ""

    m = _STRENGTH_RE.search(raw)
    strength = m.group(1).upper() if m else ""

    m = _PACK_RE.search(raw)
    pack_size = m.group(1).upper() if m else ""

    # The first substantial line is usually the brand name.
    product_name = ""
    for line in raw.splitlines():
        line = line.strip()
        if not (3 < len(line) < 60):
            continue
        if re.search(r"(?i)(nafdac|batch|lot|exp|mfg|manufactured|barcode|"
                     r"ean|upc|gtin|\d{2}/\d{2})", line):
            continue
        letters = sum(ch.isalpha() for ch in line)
        if letters < 3 or letters < len(line.replace(" ", "")) * 0.4:
            continue
        product_name = line
        break

    found = sum(bool(x) for x in (nafdac, batch, exp, manufacturer, product_name))
    confidence = round(min(0.9, 0.25 + 0.13 * found), 2)

    return Extraction(
        nafdac_number=nafdac,
        barcode=find_barcode(raw),
        product_name=product_name,
        manufacturer=manufacturer,
        batch=batch,
        exp_date=exp.raw if exp else "",
        mfg_date=mfg.raw if mfg else "",
        strength=strength,
        pack_size=pack_size,
        raw_text=raw,
        confidence=confidence,
        provider="text-parser",
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# 1. Meta vision
# --------------------------------------------------------------------------

PROMPT = """You are reading a photograph of a medicine pack for a verification tool.

Transcribe ONLY what is visible. Do not infer, complete, or correct anything.
If a field is not clearly visible, return an empty string for it.

Return strict JSON with exactly these keys:
{
  "product_name": "brand name as printed",
  "nafdac_number": "NAFDAC registration number exactly as printed, e.g. A11-0551",
  "barcode": "the digits printed under the barcode, no spaces, or empty",
  "manufacturer": "company name as printed, including 'Manufactured by' if present",
  "batch": "batch or lot number exactly as printed",
  "exp_date": "expiry date exactly as printed, e.g. EXP 03/2027",
  "mfg_date": "manufacture date exactly as printed, or empty",
  "strength": "strength as printed, e.g. 500 mg",
  "pack_size": "pack size as printed, e.g. 10 x 10's",
  "legible": true,
  "notes": "anything unusual about the printing, or empty"
}

Do not state whether the product is real or fake. You are only transcribing.
If the image is not a medicine pack, return all fields empty and legible=false."""


def extract_from_image(
    data: bytes,
    media_type: str = "image/jpeg",
    model: str | None = None,
    timeout: int = 60,
) -> Extraction:
    """Read a photograph.

    Order of preference:
      1. A vision model, if a key is configured.
      2. The OCR engine built into Windows (no key, no network).
    The model is only asked to transcribe either way. Judgement stays in
    engine.py.
    """
    meta_error: str | None = None
    if API_KEY:
        try:
            return _extract_meta(data, media_type, model, timeout)
        except VisionUnavailable as exc:
            meta_error = str(exc)

    try:
        text = windows_ocr(data)
    except OcrUnavailable as exc:
        raise VisionUnavailable(
            f"{meta_error + '. ' if meta_error else ''}"
            f"No vision model is configured and offline OCR failed ({exc}). "
            "Paste the label text instead."
        ) from exc

    if not text.strip():
        raise VisionUnavailable(
            "The photo was readable but no text was found in it. Get closer, "
            "fill the frame with the back panel, and avoid glare."
        )

    ex = parse_label_text(text)
    ex.provider = "windows-ocr"
    ex.confidence = min(ex.confidence, 0.7)
    ex.raw_text = text
    ex.warnings.insert(
        0,
        "Read offline by the Windows OCR engine. OCR confuses similar "
        "characters (0/O, 1/I, 5/S, 8/B), so check each field against the pack.",
    )
    return ex


def _extract_meta(
    data: bytes,
    media_type: str = "image/jpeg",
    model: str | None = None,
    timeout: int = 60,
) -> Extraction:
    if not API_KEY:
        raise VisionUnavailable(
            "No MODEL_API_KEY set. Vouch can still check a pack if you paste "
            "the label text or use a demo fixture."
        )
    import requests

    b64 = base64.b64encode(data).decode("ascii")
    payload = {
        "model": model or MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:{media_type};base64,{b64}"}},
            ],
        }],
    }
    try:
        r = requests.post(
            f"{API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}",
                     "Content-Type": "application/json"},
            json=payload, timeout=timeout,
        )
    except Exception as exc:                     # network
        raise VisionUnavailable(f"Could not reach the model API: {exc}") from exc

    if r.status_code == 401:
        raise VisionUnavailable("The model API rejected the key (401).")
    if r.status_code >= 400:
        raise VisionUnavailable(f"Model API error {r.status_code}: {r.text[:300]}")

    try:
        content = r.json()["choices"][0]["message"]["content"]
        body = json.loads(content) if isinstance(content, str) else content
    except Exception as exc:
        raise VisionUnavailable(f"Could not parse the model response: {exc}") from exc

    warnings = []
    if not body.get("legible", True):
        warnings.append("The model could not read this image clearly.")

    return Extraction(
        nafdac_number=_s(body.get("nafdac_number")),
        barcode=find_barcode(_s(body.get("barcode"))) or _s(body.get("barcode")),
        product_name=_s(body.get("product_name")),
        manufacturer=_s(body.get("manufacturer")),
        batch=_s(body.get("batch")),
        exp_date=_s(body.get("exp_date")),
        mfg_date=_s(body.get("mfg_date")),
        strength=_s(body.get("strength")),
        pack_size=_s(body.get("pack_size")),
        raw_text=json.dumps(body, ensure_ascii=False),
        confidence=0.85 if body.get("legible", True) else 0.35,
        provider=f"meta:{model or MODEL}",
        warnings=warnings + ([_s(body.get("notes"))] if body.get("notes") else []),
    )


def _s(v) -> str:
    return "" if v is None else str(v).strip()


# --------------------------------------------------------------------------
# 1b. Offline OCR with the engine that ships with Windows
# --------------------------------------------------------------------------

OCR_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr.ps1")


class OcrUnavailable(RuntimeError):
    pass


def windows_ocr(data: bytes, timeout: int = 60) -> str:
    """Transcribe an image with the OCR engine built into Windows.

    Two routes to the same engine: the winrt bindings (fast, in-process) and a
    PowerShell fallback (slower to start, but needs no extra package). Both work
    offline and with no API key.
    """
    import tempfile

    if not data:
        raise OcrUnavailable("empty image")

    prepared = _prepare_image(data)
    suffix = ".png"
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="vouch-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(prepared)
        errors = []
        try:
            return _ocr_winrt(path)
        except Exception as exc:
            errors.append(f"winrt: {exc}")
        try:
            return _ocr_powershell(path, timeout)
        except Exception as exc:
            errors.append(f"powershell: {exc}")
        raise OcrUnavailable("; ".join(errors))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _prepare_image(data: bytes, max_side: int = 2200) -> bytes:
    """Downscale a phone photo before OCR. Bigger is not better past a point."""
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_side:
            ratio = max_side / float(max(w, h))
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:
        return data


def _ocr_winrt(path: str) -> str:
    import asyncio

    async def run() -> str:
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage import FileAccessMode, StorageFile

        f = await StorageFile.get_file_from_path_async(path)
        stream = await f.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise OcrUnavailable("no OCR language pack installed")
        result = await engine.recognize_async(bitmap)
        return "\n".join(line.text for line in result.lines)

    return asyncio.run(run()).strip()


def _ocr_powershell(path: str, timeout: int = 60) -> str:
    import subprocess

    if not os.path.exists(OCR_SCRIPT):
        raise OcrUnavailable("ocr.ps1 is missing")
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", OCR_SCRIPT, "-Path", path],
        capture_output=True, timeout=timeout,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise OcrUnavailable(err.splitlines()[-1] if err else "OCR failed")
    return (proc.stdout or b"").decode("utf-8", "replace").strip()


def ocr_available() -> bool:
    try:
        import winrt.windows.media.ocr  # noqa: F401
        return True
    except Exception:
        pass
    try:
        import subprocess
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "if ([Windows.Media.Ocr.OcrEngine,Windows.Foundation,"
             "ContentType=WindowsRuntime]) { 'yes' }"],
            capture_output=True, timeout=20,
        )
        return b"yes" in (proc.stdout or b"")
    except Exception:
        return False


# --------------------------------------------------------------------------
# 3. Fixtures
# --------------------------------------------------------------------------

def fixtures() -> list[dict]:
    path = os.path.join(FIXTURE_DIR, "packs.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def fixture(key: str) -> dict | None:
    for f in fixtures():
        if f.get("key") == key:
            return f
    return None


def from_fixture(key: str) -> Extraction:
    f = fixture(key)
    if not f:
        raise VisionUnavailable(f"Unknown demo pack '{key}'.")
    d = f.get("fields", {})
    return Extraction(
        nafdac_number=d.get("nafdac_number", ""),
        barcode=d.get("barcode", ""),
        product_name=d.get("product_name", ""),
        manufacturer=d.get("manufacturer", ""),
        batch=d.get("batch", ""),
        exp_date=d.get("exp_date", ""),
        mfg_date=d.get("mfg_date", ""),
        strength=d.get("strength", ""),
        pack_size=d.get("pack_size", ""),
        raw_text=f.get("label_text", ""),
        confidence=1.0,
        provider="fixture",
        warnings=[],
    )


def capabilities() -> dict:
    return {
        "meta_vision": bool(API_KEY),
        "windows_ocr": ocr_available(),
        "model": MODEL,
        "base": API_BASE,
        "text_parser": True,
        "fixtures": len(fixtures()),
    }
