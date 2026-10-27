"""
Render sample pack photos for the demo.

These are not photographs of real products — they are clean label renderings
used to exercise the photo path when there is no pack on the table. The layout
matches what a Nigerian carton back panel prints: brand, strength, NAFDAC
number, batch, MFG/EXP, manufacturer.

Run:  python make_samples.py
"""

from __future__ import annotations

import os
import random

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "img")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "arialbd.ttf" if bold else "arial.ttf"
    for base in ("C:/Windows/Fonts", "/usr/share/fonts/truetype/dejavu"):
        path = os.path.join(base, name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def render(spec: dict, name: str) -> None:
    W, H = 1000, 720
    img = Image.new("RGB", (W, H), "#f3f1ec")
    d = ImageDraw.Draw(img)

    # A carton panel, slightly off-white, with a thin edge.
    d.rectangle([40, 40, W - 40, H - 40], fill="#ffffff", outline="#d9d4c8", width=3)
    d.rectangle([40, 40, W - 40, 150], fill=spec.get("band", "#0f6b4f"))
    d.text((70, 68), spec["brand"], font=font(46, True), fill="#ffffff")

    y = 180
    for line in spec["lines"]:
        d.text((70, y), line, font=font(30), fill="#16232a")
        y += 52

    # Batch and dates as a separate printed block, the way a coder prints them.
    box_y = H - 190
    d.rectangle([60, box_y, W - 60, H - 60], outline="#c9c4b8", width=2)
    d.text((80, box_y + 18), spec["batch"], font=font(30, True), fill="#16232a")
    d.text((80, box_y + 70), spec["dates"], font=font(30), fill="#16232a")

    # A little texture so it is not a flat PDF.
    px = img.load()
    rnd = random.Random(7)
    for _ in range(4000):
        x, yy = rnd.randrange(0, W), rnd.randrange(0, H)
        r, g, b = px[x, yy]
        n = rnd.randint(-6, 6)
        px[x, yy] = (max(0, min(255, r + n)), max(0, min(255, g + n)),
                     max(0, min(255, b + n)))

    os.makedirs(OUT, exist_ok=True)
    img.save(os.path.join(OUT, name), quality=92)
    print("wrote", os.path.join(OUT, name))


PACKS = [
    {
        "file": "sample-genuine.png",
        "brand": "AC-Drex Tablet",
        "band": "#0f6b4f",
        "lines": [
            "Paracetamol 500 mg  /  Caffeine 30 mg",
            "NAFDAC REG NO: A11-0551",
            "Manufactured by A.C. Drugs Ltd, Lagos",
            "10 x 10's tablets",
        ],
        "batch": "BATCH NO: ACD2401",
        "dates": "MFG 06/2025    EXP 06/2028",
    },
    {
        "file": "sample-expired.png",
        "brand": "AC-Drex Tablet",
        "band": "#0f6b4f",
        "lines": [
            "Paracetamol 500 mg  /  Caffeine 30 mg",
            "NAFDAC REG NO: A11-0551",
            "Manufactured by A.C. Drugs Ltd, Lagos",
            "10 x 10's tablets",
        ],
        "batch": "BATCH NO: ACD1902",
        "dates": "MFG 01/2023    EXP 01/2026",
    },
    {
        "file": "sample-copied.png",
        "brand": "AC-Drex Tablet",
        "band": "#7a2f2a",
        "lines": [
            "Paracetamol 500 mg  /  Caffeine 30 mg",
            "NAFDAC REG NO: A11-0551",
            "Manufactured by Emzor Pharmaceutical Industries Ltd",
            "10 x 10's tablets",
        ],
        "batch": "BATCH NO: EMZ7781",
        "dates": "MFG 09/2024    EXP 09/2027",
    },
]

if __name__ == "__main__":
    for spec in PACKS:
        render(spec, spec["file"])
