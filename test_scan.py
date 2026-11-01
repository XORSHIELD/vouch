"""Tests for the guided multi-side scanner. Run: python test_scan.py

Covers the deterministic parts that do not need a camera: frame-quality
scoring, multi-side reconciliation and conflicts, barcode/QR consensus, and the
honest failure when there is nothing to read.
"""

from __future__ import annotations

import io

import scan as S
import vision

fails = 0


def eq(label: str, got, want) -> None:
    global fails
    if got != want:
        fails += 1
        print(f"  FAIL  {label}\n        got  {got!r}\n        want {want!r}")


def yes(label: str, cond: bool, detail: str = "") -> None:
    global fails
    if not cond:
        fails += 1
        print(f"  FAIL  {label}  {detail}")


def frame(label: str, **fields) -> dict:
    base = {"confidence": 0.8}
    base.update(fields)
    return {"label": label, "extraction": base, "quality": {"verdict": "good"}}


try:
    from PIL import Image, ImageFilter
except Exception:  # pragma: no cover - PIL is a declared dependency
    Image = None

print("frame quality")
if Image is None:
    print("  SKIP  Pillow not available")
else:
    with open("samples/img/sample-expired.png", "rb") as fh:
        clean_bytes = fh.read()
    clean = S.frame_quality(clean_bytes)
    yes("clean frame scores well", clean["score"] >= 72,
        str(clean))
    yes("clean frame verdict is good", clean["verdict"] == "good", str(clean))

    base = Image.open(io.BytesIO(clean_bytes)).convert("RGB")
    blurred_buf = io.BytesIO()
    base.filter(ImageFilter.GaussianBlur(4)).save(blurred_buf, "PNG")
    blurred = S.frame_quality(blurred_buf.getvalue())
    yes("blur lowers the score", blurred["score"] < clean["score"],
        f"{blurred['score']} vs {clean['score']}")
    yes("blur is noticed", blurred["verdict"] in ("fair", "poor"), str(blurred))

    dark_buf = io.BytesIO()
    base.point(lambda v: int(v * 0.25)).save(dark_buf, "PNG")
    dark = S.frame_quality(dark_buf.getvalue())
    yes("dark frame scores below clean", dark["score"] < clean["score"],
        f"{dark['score']} vs {clean['score']}")
    yes("dark frame lighting is low", dark["lighting"] < 70, str(dark))
    yes("dark frame gives light advice", "light" in dark["advice"].lower()
        or "dark" in dark["advice"].lower(), dark["advice"])

    junk = S.frame_quality(b"not an image")
    eq("junk frame falls back", junk["verdict"], "unknown")

print("\nreconciliation")
merged, conflicts = S._merge_text([
    frame("Front panel", product_name="AC-Drex Tablet", batch="ACD1902"),
    frame("Back panel", product_name="AC-Drex Tablet", batch="ACD2401",
          exp_date="01/2026", nafdac_number="A11-0551"),
])
eq("majority product name", merged.get("product_name"), "AC-Drex Tablet")
eq("only copy of a field is used", merged.get("nafdac_number"), "A11-0551")
eq("one conflict reported", len(conflicts), 1)
eq("conflict names the field", conflicts[0]["field"], "batch")
eq("conflict keeps the majority", conflicts[0]["chosen"], "ACD1902")
eq("conflict keeps both values", len(conflicts[0]["values"]), 2)

merged2, conflicts2 = S._merge_text([
    frame("A", exp_date="01/2026"),
    frame("B", exp_date="JAN 2026"),
])
eq("equivalent dates merge", merged2.get("exp_date"), "01/2026")
eq("equivalent dates do not conflict", len(conflicts2), 0)

merged3, conflicts3 = S._merge_text([
    frame("A", batch="AB 1234"),
    frame("B", batch="ab1234"),
])
eq("punctuation and case fold", merged3.get("batch"), "AB 1234")
eq("folded codes do not conflict", len(conflicts3), 0)

merged4, _ = S._merge_text([])
eq("nothing in, nothing out", merged4, {})

print("\nbarcode consensus")
near = S._resolve_barcodes(
    [
        {"label": "Back panel", "barcode": {"value": "5010162000164",
                                            "format": "EAN-13", "votes": 2}},
        {"label": "Barcode / QR", "barcode": {"value": "5010162000165",
                                              "format": "EAN-13", "votes": 1}},
    ],
    [],
    {},
)
yes("near-miss folds into one code", near is not None, str(near))
eq("valid check digit wins", near["code"], "5010162000164")
eq("votes add up across reads", near["votes"], 3)
yes("checksum is true", near["checksum"] is True, str(near))
yes("consensus reached", near["consensus"] is True, str(near))
eq("both sides are sources", sorted(near["sources"]),
   ["Back panel", "Barcode / QR"])
eq("no alternatives left", near["alternatives"], [])

merged_b: dict = {}
split = S._resolve_barcodes(
    [
        {"label": "A", "barcode": {"value": "5010162000164", "votes": 1}},
        {"label": "B", "barcode": {"value": "4006381333931", "votes": 2}},
    ],
    [],
    merged_b,
)
eq("different codes are not folded", split["code"], "4006381333931")
eq("the other code is an alternative", [a["code"] for a in split["alternatives"]],
   ["5010162000164"])
eq("merged barcode is set", merged_b.get("barcode"), "4006381333931")

print("\nQR payloads")
codes, note = S._qr_candidates([
    "https://example.com/p/5010162000164",
    "NAFDAC No: A11-0551",
])
yes("URL tail becomes a code", "5010162000164" in codes, str(codes))
yes("NAFDAC number is found", "A11-0551" in codes, str(codes))
yes("payload is echoed as a note", "example.com" in note, note)

codes2, _ = S._qr_candidates(["https://example.com/collect?batch=ACD1902"])
eq("no false code from a non-code URL", codes2, [])

qr_merged: dict = {}
qr_out = S._resolve_barcodes(
    [{"label": "Barcode / QR", "qr": ["https://example.com/p/5010162000164"]}],
    ["https://example.com/p/5010162000164"],
    qr_merged,
)
yes("QR code resolved", qr_out is not None, str(qr_out))
eq("QR code value", qr_out["code"], "5010162000164")
yes("QR payload noted", "5010162000164" in qr_out["qr_payload"],
    str(qr_out))

print("\nlive text candidates")
strong = S.live_candidates("NAFDAC REG NO: A11-0551\nBATCH NO: ACD1902\nEXP 03/2027")
values = [c["value"] for c in strong]
yes("labelled NAFDAC is read", "A11-0551" in values, values)
yes("letter prefix is strong",
    next(c for c in strong if c["value"] == "A11-0551")["strength"] == "strong")

bare = S.live_candidates("01-2345")
eq("bare number is reported", bare[0]["value"] if bare else "", "01-2345")
eq("bare number is weak", bare[0]["strength"] if bare else "", "weak")

labelled_bare = S.live_candidates("NAFDAC No: 01-2345")
eq("label makes a bare number strong",
   labelled_bare[0]["strength"] if labelled_bare else "", "strong")

leave = S.live_candidates("NO 11-0551")
eq("a label word is not a prefix", leave[0]["value"] if leave else "", "11-0551")
eq("the number after a label word stays weak",
   leave[0]["strength"] if leave else "", "weak")
eq("number seen through OCR is repaired",
   S.live_candidates("NAFDAC All-0551")[0]["value"], "A11-0551")

date = S.live_candidates("EXP 03-2027")
eq("a date is never strong", date[0]["strength"] if date else "", "weak")

print("\nlive read")
if Image is None:
    print("  SKIP  Pillow not available")
else:
    from PIL import ImageDraw, ImageFont
    img = Image.new("RGB", (760, 460), "white")
    draw = ImageDraw.Draw(img)
    try:
        font_big = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 46)
        font = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 34)
    except OSError:
        font_big = font = ImageFont.load_default()
    draw.text((34, 30), "AC-DREX TABLET", font=font_big, fill="black")
    draw.text((34, 130), "NAFDAC REG NO: A11-0551", font=font, fill="black")
    draw.text((34, 220), "BATCH NO: ACD1902", font=font, fill="black")
    draw.text((34, 310), "EXP DATE: 03/2027", font=font, fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    label = buf.getvalue()

    try:
        read = S.live_read(label)
        eq("live reader reports its engine", read["reader"], "windows-ocr")
        yes("live reader is not AI", read["ai_used"] is False)
        yes("live reader finds the NAFDAC number",
            any(c["value"] == "A11-0551" for c in read["nafdac"]), str(read["nafdac"]))
        yes("live reader returns fields", bool(read["fields"]), str(read["fields"]))
        yes("live reader records timing", isinstance(read.get("ms"), int))
    except vision.VisionUnavailable as exc:
        print(f"  SKIP  no on-device text reader: {exc}")

    # Hints must still be reconciled when the still-frame reader fails.
    original = vision.extract_from_image

    def fail(*args, **kwargs):
        raise vision.VisionUnavailable("no reader in this test")

    vision.extract_from_image = fail
    try:
        out = S.analyze([{
            "label": "Back panel", "data": label, "media_type": "image/png",
            "barcode": None, "qr": [],
            "live": {"nafdac": "A11-0551", "strength": "strong",
                     "fields": {"batch": "ACD1902", "exp_date": "03/2027"}},
        }])
        eq("live hint fills the NAFDAC number", out["merged"]["nafdac_number"], "A11-0551")
        eq("live hint fills the batch", out["merged"]["batch"], "ACD1902")
        yes("live reader is named", "live-text" in out["readers"], str(out["readers"]))
        yes("live read is surfaced", out["live"]["nafdac"][0]["value"] == "A11-0551",
            str(out["live"]))
        yes("hint read is marked lower confidence",
            out["sides"][0]["confidence"] < 0.5, str(out["sides"][0]))
    finally:
        vision.extract_from_image = original

print("\nemergency exits")
eq("no barcodes at all", S._resolve_barcodes([{"label": "A"}], [], {}), None)
try:
    S.analyze([])
    yes("empty scan raises", False)
except vision.VisionUnavailable as exc:
    yes("empty scan raises VisionUnavailable", True, str(exc))

print()
if fails:
    print(f"{fails} FAILED")
    raise SystemExit(1)
print("all scan tests passed")
