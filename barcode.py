"""
Vouch — the second rail: barcodes.

The NAFDAC public register publishes medicines, vaccines, herbals, devices and
veterinary products. It does not publish food, drinks or cosmetics, which
NAFDAC also regulates. A consumer still needs an answer for those, and the
number they can actually read is the barcode.

So Vouch runs two rails and says which one it used:

  NAFDAC number  -> greenbook.nafdac.gov.ng        (medicines, devices, ...)
  Barcode        -> Open Food Facts / Beauty Facts (food, drink, cosmetics)

What a barcode can and cannot prove is stated plainly in the engine. It is an
identity check, not a safety check.

No API key. No account. Cached for seven days so a demo survives a bad network.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_PATH = os.path.join(HERE, "fixtures", "barcodes.json")
CACHE_DIR = os.path.join(HERE, ".cache")
CACHE_TTL = 7 * 24 * 3600
TIMEOUT = 12

UA = "Vouch/0.4 (consumer product check; +https://vouch.example)"

# Databases, in order of preference. All three are the same project and the
# same API shape, so one code path covers a drink, a lotion and a detergent.
SOURCES = [
    ("openfoodfacts", "https://world.openfoodfacts.org", "food and drink"),
    ("openbeautyfacts", "https://world.openbeautyfacts.org", "cosmetics"),
    ("openproductsfacts", "https://world.openproductsfacts.org", "household"),
]

FIELDS = (
    "code,product_name,generic_name,brands,categories,quantity,"
    "ingredients_text,allergens,countries,image_front_small_url,labels,"
    "manufacturing_places,nutriscore_grade"
)


class BarcodeUnavailable(RuntimeError):
    """The databases could not be reached. This is not a 'not found'."""


@dataclass
class Item:
    code: str
    name: str = ""
    brand: str = ""
    categories: str = ""
    quantity: str = ""
    ingredients: str = ""
    allergens: str = ""
    countries: str = ""
    image: str = ""
    labels: str = ""
    source: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "brand": self.brand,
            "categories": self.categories,
            "quantity": self.quantity,
            "ingredients": self.ingredients,
            "allergens": self.allergens,
            "countries": self.countries,
            "image": self.image,
            "labels": self.labels,
            "source": self.source,
            "url": self.url,
        }


@dataclass
class Result:
    code: str
    valid: bool | None          # check digit; None when the length is unknown
    found: bool = False
    item: Item | None = None
    message: str = ""
    signals: list[str] = field(default_factory=list)
    unavailable: bool = False
    cached: bool = False


# --------------------------------------------------------------------------
# Deterministic checks — no network needed
# --------------------------------------------------------------------------

def digits_only(raw: str) -> str:
    return re.sub(r"\D", "", str(raw or ""))


def checksum_ok(code: str) -> bool | None:
    """Is this a well-formed EAN/UPC? Catches a mistyped or invented number.

    Returns None for a length we do not recognise — that is 'unknown', not
    'invalid', because a short internal code is not necessarily wrong.
    """
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return None
    body, check = code[:-1], int(code[-1])
    total = 0
    # Weights alternate 3,1 from the rightmost body digit.
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10 == check


def kind_of(categories: str = "", name: str = "", source: str = "") -> str:
    """A coarse product kind, used only to choose the right words."""
    text = f"{categories} {name}".lower()
    if source == "openbeautyfacts":
        return "cosmetic"
    if source == "openproductsfacts":
        return "household"
    if re.search(r"\b(noodle|noodles|pasta|rice|flour|bread|biscuit|snack|"
                 r"cereal|meat|fish|oil|sugar|sauce|seasoning|soup|"
                 r"canned|confectionery|dairy|cheese|yoghurt)\b", text):
        return "food"
    if re.search(r"\b(water|waters|spring water|mineral water)\b", text):
        return "water"
    if re.search(r"\b(beverage|beverages|drink|drinks|juice|soda|cola|malt|"
                 r"energy drink|soft drink|tea|coffee)\b", text):
        return "beverage"
    if re.search(r"\b(cosmetic|cream|lotion|shampoo|soap|toothpaste|deodorant|"
                 r"perfume|makeup|skincare)\b", text):
        return "cosmetic"
    if re.search(r"\b(detergent|cleaner|bleach|disinfectant|household)\b", text):
        return "household"
    if source == "openfoodfacts":
        return "food"
    return "product"


# --------------------------------------------------------------------------
# Identity: does the code on the pack belong to the thing on the pack?
# --------------------------------------------------------------------------

_STOP = {
    "the", "and", "with", "of", "for", "a", "an", "pack", "packet", "can",
    "bottle", "sachet", "carton", "ml", "cl", "l", "g", "kg", "mg", "x",
    "original", "classic", "new", "product", "drink", "water",
}


def _tokens(text: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [t for t in raw if t not in _STOP and len(t) > 1]


def match_score(pack_text: str, item_text: str) -> float:
    """How strongly does one string name the other, 0..1.

    Plain edit distance punishes a short canonical name: "Cway" against "Cway
    Drinking Water" scores 0.35 and would accuse an honest bottle. Containment
    handles the real-world case where the database stores the brand and the
    pack prints brand + descriptor. Short names are therefore treated as a
    match when every one of their meaningful tokens appears in the longer name.
    """
    a, b = _tokens(pack_text), _tokens(item_text)
    if not a or not b:
        return 0.0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if set(short).issubset(set(long)):
        return 0.95 if len(short) > 1 else 0.9
    import difflib

    return difflib.SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()


def identity(pack_name: str, pack_brand: str, item: Item) -> dict:
    """Compare what the pack claims with what the barcode resolves to."""
    name_score = match_score(pack_name, item.name) if pack_name and item.name else 0.0
    brand_score = (match_score(pack_brand, item.brand)
                   if pack_brand and item.brand else 0.0)
    best = max(name_score, brand_score)
    if best >= 0.6:
        verdict = "match"
    elif best < 0.45:
        verdict = "different"
    else:
        verdict = "partial"
    return {
        "name_score": round(name_score, 3),
        "brand_score": round(brand_score, 3),
        "best": round(best, 3),
        "verdict": verdict,
    }



# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def _cache_path() -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, "barcodes.sqlite")


def _cache_get(code: str) -> dict | None:
    try:
        con = sqlite3.connect(_cache_path())
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS items ("
                "  code TEXT PRIMARY KEY, at REAL NOT NULL, body TEXT NOT NULL)"
            )
            row = con.execute(
                "SELECT at, body FROM items WHERE code = ?", (code,)
            ).fetchone()
        finally:
            con.close()
        if row and (time.time() - row[0]) < CACHE_TTL:
            return json.loads(row[1])
    except Exception:
        pass
    return None


def _cache_put(code: str, body: dict) -> None:
    try:
        con = sqlite3.connect(_cache_path())
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS items ("
                "  code TEXT PRIMARY KEY, at REAL NOT NULL, body TEXT NOT NULL)"
            )
            con.execute(
                "INSERT OR REPLACE INTO items (code, at, body) VALUES (?,?,?)",
                (code, time.time(), json.dumps(body)),
            )
            con.commit()
        finally:
            con.close()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Fixtures — the demo must not depend on someone else's uptime
# --------------------------------------------------------------------------

def _fixtures() -> dict:
    try:
        with open(FIXTURE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"products": {}, "demos": []}


def fixture_product(code: str) -> dict | None:
    return (_fixtures().get("products") or {}).get(code)


def demos() -> list[dict]:
    return _fixtures().get("demos") or []


# --------------------------------------------------------------------------
# Live lookup
# --------------------------------------------------------------------------

def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _query(base: str, code: str) -> dict | None:
    url = f"{base}/api/v2/product/{urllib.parse.quote(code)}.json?fields={FIELDS}"
    try:
        payload = _fetch(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise BarcodeUnavailable(str(exc)) from exc
    except Exception as exc:
        raise BarcodeUnavailable(str(exc)) from exc
    if str(payload.get("status")) not in ("1", "success"):
        return None
    product = payload.get("product") or {}
    return product or None


def _from_product(code: str, p: dict, source: str, base: str) -> Item:
    return Item(
        code=code,
        name=(p.get("product_name") or "").strip(),
        brand=(p.get("brands") or "").strip(),
        categories=(p.get("categories") or "").strip(),
        quantity=(p.get("quantity") or "").strip(),
        ingredients=(p.get("ingredients_text") or "").strip()[:900],
        allergens=(p.get("allergens") or "").strip()[:300],
        countries=(p.get("countries") or "").strip(),
        image=(p.get("image_front_small_url") or "").strip(),
        labels=(p.get("labels") or "").strip()[:300],
        source=source,
        url=f"{base}/product/{code}",
    )


def lookup(code: str, offline: bool = False, use_fixtures: bool = True) -> Result:
    """Resolve a barcode to a product record.

    A network failure raises nothing: it returns `unavailable=True` so the
    engine can say "I could not ask" instead of "not found". That distinction
    is the whole argument of this product.
    """
    code = digits_only(code)
    if not code:
        return Result(code="", valid=None, message="No barcode given.")

    valid = checksum_ok(code)
    signals: list[str] = []

    cached = _cache_get(code)
    if cached is not None:
        item = Item(**cached)
        return Result(code, valid, True, item, cached=True)

    fx = fixture_product(code) if use_fixtures else None
    if fx:
        item = _from_product(code, fx, fx.get("_source", "fixture"),
                             fx.get("_base", "https://world.openfoodfacts.org"))
        _cache_put(code, item.to_dict())
        return Result(code, valid, True, item, message="Demo record.", cached=True)

    if offline:
        if valid is None:
            raise BarcodeUnavailable(
                f"offline, and '{code}' is not a length this tool recognises"
            )
        return Result(code, valid, False, None,
                      message="Offline, and this barcode is not in the local cache.",
                      unavailable=True)

    # Try every database. A drink will only be in one of them.
    tried = []
    for name, base, _label in SOURCES:
        try:
            p = _query(base, code)
        except BarcodeUnavailable as exc:
            tried.append(f"{name}: {exc}")
            continue
        if p:
            item = _from_product(code, p, name, base)
            _cache_put(code, item.to_dict())
            return Result(code, valid, True, item)

    if tried and len(tried) == len(SOURCES):
        raise BarcodeUnavailable("; ".join(tried))

    if valid is False:
        signals.append(
            "The check digit fails, so this cannot be a correctly printed "
            "EAN/UPC barcode. It is most often a mistyped digit."
        )
    return Result(
        code, valid, False, None,
        message="No record of this barcode in any consumer product database.",
        signals=signals,
    )


def search(term: str, limit: int = 5) -> list[Item]:
    """Search by name, for when the barcode is unreadable. Best-effort."""
    term = (term or "").strip()
    if not term:
        return []
    out: list[Item] = []
    for name, base, _label in SOURCES:
        url = (f"{base}/cgi/search.pl?search_terms={urllib.parse.quote(term)}"
               f"&json=1&page_size={min(limit, 10)}&fields={FIELDS}")
        try:
            payload = _fetch(url)
        except Exception:
            continue
        for p in payload.get("products") or []:
            code = str(p.get("code") or "")
            if not code:
                continue
            item = _from_product(code, p, name, base)
            if item.name:
                out.append(item)
        if out:
            break
    return out[:limit]


def capabilities() -> dict:
    return {
        "barcode": True,
        "sources": [{"id": n, "covers": label} for n, _b, label in SOURCES],
        "fixtures": len(demos()),
        "cached": _cached_count(),
    }


def _cached_count() -> int:
    try:
        con = sqlite3.connect(_cache_path())
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS items ("
                "  code TEXT PRIMARY KEY, at REAL NOT NULL, body TEXT NOT NULL)"
            )
            return con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        finally:
            con.close()
    except Exception:
        return 0
