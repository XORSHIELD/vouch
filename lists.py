"""
Vouch — bulk ingest for NAFDAC published lists.

When NAFDAC publishes registered foods, waters, or cosmetics outside
the CAPTCHA wall (a bulletin, a gazette extract, a partner file),
this module files them into the NAPAMS seed so every install learns
them at once. Provenance stays honest: list rows are cited as the
published list they came from, never as portal replies.

Accepted shapes: CSV or a JSON list of objects. Required columns are
number + product; maker, category, approved, expiry, and status are
optional. Column names may use any of the aliases below.

Run:  python lists.py bulletin.csv --name "Registered Foods" \\
          --date 2026-10-01
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import time

import napams

_ALIASES = {
    "number": ("number", "nafdac", "nafdac_no", "no", "cert"),
    "product": ("product", "product_name", "name", "brand"),
    "maker": ("maker", "manufacturer", "holder", "company", "applicant"),
    "category": ("category", "product_category", "type", "kind"),
    "approved": ("approved", "date_approved", "approval_date", "reg_date"),
    "expiry": ("expiry", "expiry_date", "exp_date", "valid_until"),
    "status": ("status", "verdict", "state", "registration_status"),
}

_MAX_TEXT = 120


def _canon(number: str) -> str:
    """Same key rule as napams.known: digits and capitals only."""
    return re.sub(r"[^A-Z0-9]", "", (number or "").upper())


def _clean_product(raw: str) -> str:
    val = re.sub(r"\s+", " ", (raw or "")).strip()
    return re.split(r"\s*\(", val)[0].strip() or val


def _split_label(label: str) -> tuple[str, str]:
    """'Name, YYYY-MM-DD' -> (label, date); bad dates mean today."""
    date = (label or "").rsplit(",", 1)[-1].strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        date = time.strftime("%Y-%m-%d")
    return (label or "").strip(), date


def _field(row: dict, want: str) -> str:
    low = {(k or "").strip().lower(): (v or "")
           for k, v in (row or {}).items()}
    for alias in _ALIASES[want]:
        if alias in low:
            return re.sub(r"\s+", " ", str(low[alias])).strip()
    return ""


def _entry(row: dict, label: str, date: str) -> dict | None:
    number = _field(row, "number")
    product = _clean_product(_field(row, "product"))
    if not number or not product:
        return None
    status = _field(row, "status").lower()
    return {
        "number": number,
        "product": product,
        "maker": _field(row, "maker")[:_MAX_TEXT],
        "category": _field(row, "category")[:_MAX_TEXT],
        "approved": _field(row, "approved")[:_MAX_TEXT],
        "expiry": _field(row, "expiry")[:_MAX_TEXT],
        "verdict": (napams.EXPIRED if "expir" in status
                    else napams.REGISTERED),
        "first_seen": date,
        "hits": 1,
        "source": "list",
        "list": label,
    }


def load_csv(text: str, label: str) -> tuple[dict, list]:
    """Parse CSV text. Returns (entries by key, error lines)."""
    label, date = _split_label(label)
    entries: dict[str, dict] = {}
    errors: list[str] = []
    try:
        reader = csv.DictReader(io.StringIO(text or ""))
        for row in reader:
            ent = _entry(row or {}, label, date)
            if ent:
                entries[_canon(ent["number"])] = ent
            else:
                errors.append(f"line {reader.line_num}: "
                              "number and product required")
    except Exception as e:
        errors.append(f"unreadable csv: {e}")
    return entries, errors


def load_json(text: str, label: str) -> tuple[dict, list]:
    """Parse a JSON list of objects. Returns (entries, error lines)."""
    label, date = _split_label(label)
    entries: dict[str, dict] = {}
    errors: list[str] = []
    try:
        body = json.loads(text or "")
    except ValueError as e:
        return entries, [f"unreadable json: {e}"]
    if not isinstance(body, list):
        return entries, ["json must be a list of objects"]
    for i, row in enumerate(body, 1):
        if not isinstance(row, dict):
            errors.append(f"row {i}: not an object")
            continue
        ent = _entry(row, label, date)
        if ent:
            entries[_canon(ent["number"])] = ent
        else:
            errors.append(f"row {i}: number and product required")
    return entries, errors


def merge(seed_path: str, entries: dict) -> dict:
    """File entries into the seed. Never raises on bad files."""
    counts = {"added": 0, "kept": 0, "updated": 0}
    try:
        with open(seed_path, encoding="utf-8") as fh:
            seed = json.load(fh)
        if not isinstance(seed, dict):
            seed = {}
    except (OSError, ValueError):
        seed = {}
    for key, ent in (entries or {}).items():
        prev = seed.get(key)
        if prev is None:
            seed[key] = ent
            counts["added"] += 1
        elif (prev or {}).get("list") == ent.get("list"):
            seed[key] = ent
            counts["updated"] += 1
        else:
            counts["kept"] += 1
    try:
        with open(seed_path, "w", encoding="utf-8") as fh:
            json.dump(seed, fh, ensure_ascii=False, indent=2,
                      sort_keys=True)
            fh.write("\n")
    except OSError:
        return {"added": 0, "kept": 0, "updated": 0}
    return counts


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0].startswith("-"):
        print("usage: python lists.py FILE --name NAME --date YYYY-MM-DD"
              " [--seed PATH]")
        return 2
    path = args.pop(0)
    name, date, seed = "", "", napams.SEED_FILE
    i = 0
    while i < len(args):
        if args[i] == "--name" and i + 1 < len(args):
            name = args[i + 1]
            i += 2
        elif args[i] == "--date" and i + 1 < len(args):
            date = args[i + 1]
            i += 2
        elif args[i] == "--seed" and i + 1 < len(args):
            seed = args[i + 1]
            i += 2
        else:
            i += 1
    label = f"{name}, {date}" if name else date
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        print(f"cannot read {path}: {e}")
        return 1
    loader = load_json if path.lower().endswith(".json") else load_csv
    entries, errors = loader(text, label)
    counts = merge(seed, entries)
    print(f"added={counts['added']} kept={counts['kept']}"
          f" updated={counts['updated']} errors={len(errors)}")
    for err in errors[:20]:
        print(" ", err)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
