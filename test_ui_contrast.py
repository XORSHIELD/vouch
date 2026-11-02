"""Dark-mode contrast for the Verify verdict card. Run: python test_ui_contrast.py

The verdict band and the compare columns carry near-white (--ink) text in
dark mode, so their dark-theme surfaces must be dark too. The @supports
fallback block does not count: it only applies where backdrop-filter is
missing, while the bug shows up in normal browsers.
"""

from __future__ import annotations

import re

fails = 0


def yes(label: str, cond: bool, detail: str = "") -> None:
    global fails
    if not cond:
        fails += 1
        print(f"  FAIL  {label}  {detail}")


def strip_fallbacks(css: str) -> str:
    out = css
    while True:
        i = out.find("@supports not")
        if i < 0:
            return out
        j = out.find("{", i)
        depth = 0
        k = j
        while k < len(out):
            if out[k] == "{":
                depth += 1
            elif out[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        out = out[:i] + out[k + 1:]


def dark_vars(css: str) -> dict:
    m = re.search(r'data-theme="dark"\]\s*\{([^}]*)\}', css)
    vars_ = {}
    if m:
        for name, val in re.findall(r'(--[\w-]+)\s*:\s*([^;]+);', m.group(1)):
            vars_[name.strip()] = val.strip()
    return vars_


def parse_color(val: str, vars_: dict) -> tuple:
    val = val.strip()
    m = re.match(r'var\((--[\w-]+)\)', val)
    if m:
        return parse_color(vars_.get(m.group(1), "#000"), vars_)
    m = re.match(r'#([0-9a-fA-F]{6})$', val)
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    m = re.match(r'rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)', val)
    if m:
        return (float(m.group(1)), float(m.group(2)), float(m.group(3)),
                float(m.group(4)) if m.group(4) is not None else 1.0)
    raise ValueError(f"unparseable color {val!r}")


def luminance(rgb: tuple) -> float:
    def lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def over(fg: tuple, bg: tuple) -> tuple:
    a = fg[3]
    return (fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a),
            fg[2] * a + bg[2] * (1 - a), 1.0)


def dark_rule_bg(css: str, selector: str) -> str | None:
    for m in re.finditer(r'\[data-theme="dark"\]([^{}]*)\{([^}]*)\}', css):
        if re.search(selector, m.group(1)):
            b = re.search(r'background(?:-color)?\s*:\s*([^;}]+)', m.group(2))
            if b:
                return b.group(1).strip()
    return None


html = open("ui/index.html", encoding="utf-8").read()
css = strip_fallbacks(html)
vars_ = dark_vars(css)
page = parse_color(vars_["--bg"], vars_)

print("dark verdict band")
bg = dark_rule_bg(css, r'\.band(?![\w-])')
yes("band has a dark-theme background rule outside the fallback", bg is not None)
if bg is not None:
    lum = luminance(over(parse_color(bg, vars_), page))
    yes("band surface stays dark behind near-white text", lum < 0.25, f"{bg} lum={lum:.3f}")

print("\ndark compare columns")
for sel, name in ((r'\.compare\s*>\s*div:first-child', "on-the-pack column"),
                  (r'\.compare\s*>\s*div:last-child', "registry column")):
    bg = dark_rule_bg(css, sel)
    yes(f"{name} has a dark-theme background rule", bg is not None)
    if bg is not None:
        lum = luminance(over(parse_color(bg, vars_), page))
        yes(f"{name} surface stays dark", lum < 0.25, f"{bg} lum={lum:.3f}")

print("\nlight band unchanged")
root = {}
m = re.search(r':root\s*\{([^}]*)\}', css)
if m:
    for name, val in re.findall(r'(--[\w-]+)\s*:\s*([^;]+);', m.group(1)):
        root[name.strip()] = val.strip()
page_light = parse_color(root["--bg"], root)
light_bg = None
for m in re.finditer(r'\.band\s*\{([^}]*)\}', css):
    if '[data-theme="dark"]' in css[max(0, m.start() - 200):m.start()]:
        continue
    b = re.search(r'background(?:-color)?\s*:\s*([^;}]+)', m.group(1))
    if b:
        light_bg = b.group(1).strip()
yes("light band keeps a light glass background", light_bg is not None)
if light_bg is not None:
    lum = luminance(over(parse_color(light_bg, root), page_light))
    yes("light band surface stays light", lum > 0.6, f"{light_bg} lum={lum:.3f}")

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
