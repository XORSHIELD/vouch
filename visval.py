"""Photo validation, live and degraded.

Usage:
    python visval.py           validate the 3 sample packs via the server
    python visval.py degrade   local robustness battery on degraded copies

The live mode reads the sample pack photos through /api/extract/image,
feeds any NAFDAC number found into /api/check, and exercises the
pasted-text path. Read-only: it creates no cases and changes no data.
The degrade mode runs the same photos plus rotated/blurred/noisy/flat
variants through the in-process extractor and reports what survives.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

if not os.environ.get("no_proxy"):
    os.environ["no_proxy"] = "127.0.0.1,localhost"
if not os.environ.get("NO_PROXY"):
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"

BASE = os.environ.get("VISVAL_BASE", "http://127.0.0.1:8091")
SAMPLES = ("sample-genuine.png", "sample-copied.png", "sample-expired.png")


def post_json(path: str, body: dict):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.status, json.loads(r.read().decode())


def post_file(path: str, fname: str, data: bytes):
    bnd = "----visval"
    head = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{fname}\"\r\nContent-Type: image/png\r\n\r\n").encode()
    req = urllib.request.Request(
        BASE + path, data=head + data + f"\r\n--{bnd}--\r\n".encode(),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def main() -> int:
    for name in SAMPLES:
        with open(f"samples/img/{name}", "rb") as fh:
            raw = fh.read()
        s, out = post_file("/api/extract/image", name, raw)
        if s != 200:
            print(f"{name}: EXTRACT FAILED {s} {out}")
            continue
        ex = out["extraction"]
        got = {k: ex.get(k) for k in
               ("provider", "confidence", "nafdac_number", "product_name",
                "manufacturer", "batch", "exp_date", "mfg_date", "pin")}
        got["warnings"] = [w[:70] for w in ex.get("warnings", [])]
        print(f"{name}: {json.dumps(got)}")
        if ex.get("nafdac_number"):
            _, a = post_json("/api/check", {
                "nafdac_number": ex["nafdac_number"],
                "manufacturer": ex.get("manufacturer", ""),
                "batch": ex.get("batch", ""),
                "exp_date": ex.get("exp_date", "")})
            print(f"  -> check: band={a.get('band')} "
                  f"headline={(a.get('headline') or '')[:90]}")

    s, out = post_json("/api/extract/text", {"text":
        "AC-DREX Tablet\nNAFDAC Reg No A11-0551\nBatch EMZ7781\nEXP 03/2027\n"
        "Manufactured by A.C. Drugs Ltd"})
    ex = out.get("extraction", out)
    print("pasted-text:", json.dumps({k: ex.get(k) for k in
          ("provider", "nafdac_number", "batch", "exp_date")}))
    print("VISVAL-DONE")
    return 0


def variants(data: bytes) -> list[tuple[str, bytes]]:
    """Degraded copies of one pack photo: rotation, blur, noise, flat."""
    import io
    import random

    from PIL import Image, ImageEnhance, ImageFilter

    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    out: list[tuple[str, bytes]] = []

    def pack(im) -> bytes:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()

    out.append(("rotate12", pack(img.rotate(12, expand=True,
                                           fillcolor="white"))))
    out.append(("blur2", pack(img.filter(ImageFilter.GaussianBlur(2)))))
    noisy = img.copy()
    px = noisy.load()
    rnd = random.Random(7)
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            r, g, b = px[x, y]
            n = rnd.randint(-40, 40)
            px[x, y] = (max(0, min(255, r + n)),
                        max(0, min(255, g + n)),
                        max(0, min(255, b + n)))
    out.append(("noise", pack(noisy)))
    out.append(("flat", pack(ImageEnhance.Contrast(img).enhance(0.4))))
    return out


def battery() -> int:
    """Run every sample plus its degraded variants. Reports, never fails."""
    import vision
    from engine import PackInput, assess

    for name in SAMPLES:
        with open(f"samples/img/{name}", "rb") as fh:
            raw = fh.read()
        for kind, data in [("clean", raw)] + variants(raw):
            try:
                ex = vision.extract_from_image(data, "image/png")
            except Exception as exc:
                print(f"{name} {kind}: UNREADABLE {str(exc)[:80]}")
                continue
            a = assess(PackInput(nafdac_number=ex.nafdac_number,
                                 manufacturer=ex.manufacturer,
                                 batch=ex.batch, exp_date=ex.exp_date))
            print(f"{name} {kind}: nafdac={ex.nafdac_number!r} "
                  f"batch={ex.batch!r} exp={ex.exp_date!r} "
                  f"band={a.band}")
    print("BATTERY-DONE")
    return 0


if __name__ == "__main__":
    sys.exit(battery() if len(sys.argv) > 1 and sys.argv[1] == "degrade"
             else main())
