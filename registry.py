"""
Vouch — NAFDAC registry client.

Live, key-less access to the NAFDAC Greenbook registry (greenbook.nafdac.gov.ng),
plus the normalization layer that the official lookup lacks.

Why normalization matters (measured, Oct 2026):
    The registry stores the same numbering series in two forms.
    "A3-1450" -> 0 results.  "03-1450" -> 1 result.
    A genuine pack printed A3-1450 therefore reads as UNREGISTERED
    on the official search. Naive matching produces false counterfeit
    flags. This module resolves the variants before deciding anything.

Design rules:
  - Never return a binary fake/genuine verdict.
  - Every answer carries evidence (the raw record) and a confidence level.
  - Cache aggressively; the registry is a public government service.
  - stdlib only, so it runs anywhere without pip.

Usage:
    python registry.py A11-0551
    python registry.py A3-1450
    python registry.py --name paracetamol --limit 5
    python registry.py A11-0551 --manufacturer "ACME Ltd" --json
    python registry.py --sync            # mirror the whole listing locally
    python registry.py --sync-status     # how fresh the mirror is
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Any

BASE = "https://greenbook.nafdac.gov.ng"
UA = (
    "Vouch/0.1 (consumer product verification research; "
    "contact: vouch-demo@example.com)"
)
CACHE_TTL = 60 * 60 * 24 * 7      # 7 days
MIN_INTERVAL = 0.6                 # seconds between live requests
SYNC_STALE_AFTER = 30 * 60         # a sync silent this long is presumed dead
SYNC_STALE_DAYS = 7              # a mirror older than this warns
CACHE_DIR = (os.environ.get("VOUCH_CACHE_DIR")
             or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             ".cache"))

# Datatable columns the registry expects for the product listing endpoint.
COLUMNS = [
    ("product_name", "product_name"),
    ("ingredient.ingredient_name", "ingredient.ingredient_name"),
]


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------

_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
# OCR confusions, applied only inside digit fields.
_OCR_TO_DIGIT = str.maketrans({
    "O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "|": "1",
    "S": "5", "B": "8", "Z": "2", "G": "6", "T": "7",
})
# A letter that OCR produces when the true character is a digit.
_DIGIT_LOOKING = set("OQDI LSZBG".replace(" ", ""))


def normalise(raw: str) -> list[str]:
    """Safe canonical forms for a printed NAFDAC number, best first.

    HARD RULE: this never converts between numbering series. The alpha series
    and the numeric series are DISJOINT, and they collide on the same tail.
    Measured on the live registry (Oct 2026):
        04-0271 = "Sam C.P.M."        but  A4-0271 = "Avstat 10 Tablets"
        04-7750 = "Sanarteme Inj."    but  A4-7750 = "Cledibact Tablet"
        04-9672 = "Sandimmun Neoral"  but  A4-9672 = "Ostomax Tablet"
    Converting "A4-x" to "04-x" therefore matches a DIFFERENT product. That is
    how a verification tool ends up accusing a genuine pack. We only repair
    OCR noise inside a series and normalise punctuation.

    Use explore_variants() for the disjoint spelling, and only for display.
    """
    if not raw:
        return []

    s = str(raw).strip().upper()
    for d in _DASHES:
        s = s.replace(d, "-")
    s = re.sub(r"[\s._/]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        return []

    # Split into (letter prefix, series, tail). A printed number has no
    # separators as often as it has them: "B4-0262" is also printed "B40262".
    # The split is not cosmetic — reading it wrong loses a real registration,
    # which is indistinguishable from an unregistered product.
    triples: list[tuple[str, str, str]] = []
    if "-" in s:
        m = re.match(
            r"^([A-Z]{0,2})-?(\d{1,2}|[A-Z0-9]{1,2})-?([A-Z0-9]{3,7})$", s)
        if m:
            triples.append(m.groups())
    else:
        # No separator: try every shape the registry actually uses, tail first.
        # 4- and 6-digit tails cover 99.5% of the register. Series length is
        # tried longest-first because two-digit series like "11" and "04" are
        # the common case.
        compact = re.sub(r"[^A-Z0-9]", "", s)
        for tail_len in (4, 6):
            for series_len in (2, 1):
                m = re.match(
                    rf"^([A-Z]{{0,2}})(\d{{{series_len}}})"
                    rf"([A-Z0-9]{{{tail_len}}})$", compact)
                if m:
                    triples.append(m.groups())
        if not triples:
            m = re.match(
                r"^([A-Z]{0,2})-?(\d{1,2}|[A-Z0-9]{1,2})-?([A-Z0-9]{3,7})$", s)
            if m:
                triples.append(m.groups())

    if not triples:
        return [s]

    out: list[str] = []

    def add(v: str) -> None:
        if v and v not in out:
            out.append(v)

    for alpha, series, tail in triples:
        # 1. Exactly as printed (punctuation normalised). Most packs are correct.
        add(f"{alpha}{series}-{tail}" if alpha else f"{series}-{tail}")

        # 2. Repair OCR noise inside the digit fields.
        s_rep = series.translate(_OCR_TO_DIGIT)
        t_rep = tail.translate(_OCR_TO_DIGIT)
        if s_rep.isdigit() and t_rep.isdigit():
            add(f"{alpha}{s_rep}-{t_rep}" if alpha else f"{s_rep}-{t_rep}")

        # 3. A single letter where the true first character is a digit
        #    ("O3-1450" read off a pack that prints "03-1450").
        if len(alpha) == 1 and alpha in _DIGIT_LOOKING and s_rep.isdigit():
            add(f"{alpha.translate(_OCR_TO_DIGIT)}{s_rep}-{t_rep}")
        # 4. A digit-looking letter inside a two-letter prefix ("AI1" -> "A11").
        elif len(alpha) >= 2 and s_rep.isdigit():
            add(f"{alpha[0]}{(alpha[1:] + s_rep).translate(_OCR_TO_DIGIT)}-{t_rep}")

    return out


def explore_variants(printed: str) -> list[str]:
    """The disjoint spelling of a number. DISPLAY ONLY.

    Never feed this into a verdict: `04-0271` and `A4-0271` are different
    products. Surface it to a human as "also exists in the other series".
    """
    cands = normalise(printed)
    if not cands:
        return []
    head, _, tail = cands[0].partition("-")
    if not tail:
        return []
    if head[:1].isalpha():
        num = head[1:]
        return [f"{int(num):02d}-{tail}"] if num.isdigit() else []
    if head.isdigit():
        return [f"A{int(head)}-{tail}"]
    return []


# --------------------------------------------------------------------------
# Transport + cache
# --------------------------------------------------------------------------

_last_call = [0.0]


def _throttle() -> None:
    gap = time.time() - _last_call[0]
    if gap < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - gap)
    _last_call[0] = time.time()


def _cache_path() -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, "registry.sqlite")


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(_cache_path())
    con.execute(
        "CREATE TABLE IF NOT EXISTS lookups ("
        "  q TEXT PRIMARY KEY, at REAL NOT NULL, body TEXT NOT NULL)"
    )
    return con


def _cache_get(q: str) -> dict | None:
    try:
        con = _db()
        try:
            row = con.execute(
                "SELECT at, body FROM lookups WHERE q = ?", (q,)
            ).fetchone()
        finally:
            con.close()
        if row and (time.time() - row[0]) < CACHE_TTL:
            return json.loads(row[1])
    except Exception:
        pass
    return None


def _cache_put(q: str, body: dict) -> None:
    try:
        con = _db()
        try:
            con.execute(
                "INSERT OR REPLACE INTO lookups (q, at, body) VALUES (?,?,?)",
                (q, time.time(), json.dumps(body)),
            )
            con.commit()
        finally:
            con.close()
    except Exception:
        pass


def _query_string(term: str, length: int) -> str:
    parts = [("draw", "1")]
    for i, (data, name) in enumerate(COLUMNS):
        parts += [
            (f"columns[{i}][data]", data),
            (f"columns[{i}][name]", name),
            (f"columns[{i}][searchable]", "true"),
            (f"columns[{i}][orderable]", "true"),
            (f"columns[{i}][search][value]", ""),
            (f"columns[{i}][search][regex]", "false"),
        ]
    parts += [
        ("order[0][column]", "0"),
        ("order[0][dir]", "asc"),
        ("start", "0"),
        ("length", str(length)),
        ("search[value]", term),
        ("search[regex]", "false"),
    ]
    return urllib.parse.urlencode(parts)


def _fetch(term: str, length: int, start: int) -> dict:
    """One live listing request. No cache reads or writes."""
    parts = [("draw", "1")]
    for i, (data, name) in enumerate(COLUMNS):
        parts += [
            (f"columns[{i}][data]", data),
            (f"columns[{i}][name]", name),
            (f"columns[{i}][searchable]", "true"),
            (f"columns[{i}][orderable]", "true"),
            (f"columns[{i}][search][value]", ""),
            (f"columns[{i}][search][regex]", "false"),
        ]
    parts += [
        ("order[0][column]", "0"),
        ("order[0][dir]", "asc"),
        ("start", str(start)),
        ("length", str(length)),
        ("search[value]", term),
        ("search[regex]", "false"),
    ]
    qs = urllib.parse.urlencode(parts)
    _throttle()
    req = urllib.request.Request(
        f"{BASE}/?{qs}",
        headers={
            "User-Agent": UA,
            "Referer": f"{BASE}/",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError,
            json.JSONDecodeError) as exc:
        raise RegistryUnavailable(str(exc)) from exc


def search(term: str, length: int = 10, offline: bool = False, start: int = 0) -> dict:
    """Search the registry. Returns the raw datatables payload.

    Once a sync has completed, the local mirror answers: same shape, same
    records, no network. Otherwise this is a live lookup with a 7-day cache.
    """
    term = (term or "").strip()
    if mirror_ready():
        return search_mirror(term, length, start)
    key = f"s|{term.lower()}|{length}|{start}"

    cached = _cache_get(key)
    if cached is not None:
        return cached
    if offline:
        # An empty offline cache must never look like a real zero-result answer.
        # "not found" is an accusation; "I could not ask" is not.
        raise RegistryUnavailable(
            f"offline, and no cached answer for '{term}'"
        )

    body = _fetch(term, length, start)
    _cache_put(key, body)
    return body


class RegistryUnavailable(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Local mirror — a synced copy of the whole Greenbook
# --------------------------------------------------------------------------
#
# Live lookup answers one number at a time. The mirror holds every record
# locally instead: faster, works offline, and kind to a public government
# server. sync_run() pages the listing endpoint (empty search, start/length)
# and stores each record verbatim; search() prefers the mirror once a
# completed pass exists and otherwise behaves exactly as before.

MIRROR_SCHEMA = """
CREATE TABLE IF NOT EXISTS mirror (
  product_id TEXT PRIMARY KEY,
  nafdac TEXT NOT NULL DEFAULT '',
  nafdac_norm TEXT NOT NULL DEFAULT '',
  product_name TEXT NOT NULL DEFAULT '',
  manufacturer TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '',
  raw TEXT NOT NULL DEFAULT '{}',
  pass INTEGER NOT NULL DEFAULT 0,
  synced_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mirror_nafdac ON mirror(nafdac_norm);
CREATE INDEX IF NOT EXISTS idx_mirror_name ON mirror(product_name);
CREATE TABLE IF NOT EXISTS sync_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  status TEXT NOT NULL DEFAULT 'never',
  started_at REAL NOT NULL DEFAULT 0,
  finished_at REAL NOT NULL DEFAULT 0,
  total INTEGER NOT NULL DEFAULT 0,
  fetched INTEGER NOT NULL DEFAULT 0,
  pass INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT ''
);
"""


def _mirror_db() -> sqlite3.Connection:
    con = _db()
    con.executescript(MIRROR_SCHEMA)
    return con


_SYNC_KEYS = ("status", "started_at", "finished_at", "total", "fetched",
              "pass", "error")


def _sync_get() -> dict:
    try:
        con = _mirror_db()
        try:
            row = con.execute(
                "SELECT status, started_at, finished_at, total, fetched,"
                " pass, error FROM sync_state WHERE id = 1"
            ).fetchone()
        finally:
            con.close()
    except Exception:
        row = None
    if not row:
        return {"status": "never", "started_at": 0, "finished_at": 0,
                "total": 0, "fetched": 0, "pass": 0, "error": ""}
    return dict(zip(_SYNC_KEYS, row))


def _sync_put(**kw) -> dict:
    st = _sync_get()
    if "pass_no" in kw:      # "pass" is a keyword, so callers say pass_no
        kw["pass"] = kw.pop("pass_no")
    st.update({k: v for k, v in kw.items() if k in _SYNC_KEYS})
    con = _mirror_db()
    try:
        con.execute(
            "INSERT OR REPLACE INTO sync_state"
            " (id, status, started_at, finished_at, total, fetched,"
            " pass, error) VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
            tuple(st[k] for k in _SYNC_KEYS),
        )
        con.commit()
    finally:
        con.close()
    return st


def sync_running() -> bool:
    st = _sync_get()
    return (st["status"] == "running"
            and (time.time() - st["started_at"]) < SYNC_STALE_AFTER)


def mirror_ready() -> bool:
    try:
        con = _mirror_db()
        try:
            row = con.execute(
                "SELECT status FROM sync_state WHERE id = 1").fetchone()
            if not row or row[0] != "complete":
                return False
            return con.execute(
                "SELECT COUNT(*) FROM mirror").fetchone()[0] > 0
        finally:
            con.close()
    except Exception:
        return False


def sync_status() -> dict:
    """How fresh the local mirror is. Cheap: two indexed reads."""
    st = _sync_get()
    try:
        con = _mirror_db()
        try:
            records = con.execute(
                "SELECT COUNT(*) FROM mirror").fetchone()[0]
        finally:
            con.close()
    except Exception:
        records = 0
    st["records"] = records
    st["ready"] = st["status"] == "complete" and records > 0
    st["source"] = BASE
    finished = st.get("finished_at") or 0
    st["age_days"] = round((time.time() - finished) / 86400, 1) if finished else None
    st["stale"] = bool(st["ready"]) and (st["age_days"] or 0) > SYNC_STALE_DAYS
    return st


def _mirror_upsert(con: sqlite3.Connection, d: dict, pass_no: int) -> None:
    rec = Record.from_api(d)
    norms = normalise(rec.nafdac)
    pid = str(d.get("product_id") or "").strip()
    if not pid:
        pid = f"n:{rec.nafdac}|{rec.product_name}|{rec.manufacturer}"
    con.execute(
        "INSERT OR REPLACE INTO mirror"
        " (product_id, nafdac, nafdac_norm, product_name, manufacturer,"
        " status, raw, pass, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, rec.nafdac, norms[0] if norms else rec.nafdac,
         rec.product_name, rec.manufacturer, rec.status,
         json.dumps(d), pass_no, time.time()),
    )


def _like_escape(term: str) -> str:
    return (term.replace("\\", "\\\\").replace("%", "\\%")
               .replace("_", "\\_"))


def search_mirror(term: str, length: int = 10, start: int = 0) -> dict:
    """The same datatables-shaped answer as search(), from the local copy."""
    term = (term or "").strip().lower()
    con = _mirror_db()
    try:
        total = con.execute("SELECT COUNT(*) FROM mirror").fetchone()[0]
        if term:
            like = f"%{_like_escape(term)}%"
            where = ("WHERE lower(nafdac) LIKE ? ESCAPE '\\'"
                     " OR lower(nafdac_norm) LIKE ? ESCAPE '\\'"
                     " OR lower(product_name) LIKE ? ESCAPE '\\'"
                     " OR lower(manufacturer) LIKE ? ESCAPE '\\'")
            args: list = [like] * 4
            filtered = con.execute(
                f"SELECT COUNT(*) FROM mirror {where}", args).fetchone()[0]
            rows = con.execute(
                f"SELECT raw FROM mirror {where}"
                " ORDER BY product_name ASC LIMIT ? OFFSET ?",
                args + [length, start]).fetchall()
        else:
            filtered = total
            rows = con.execute(
                "SELECT raw FROM mirror ORDER BY product_name ASC"
                " LIMIT ? OFFSET ?", (length, start)).fetchall()
    finally:
        con.close()
    return {
        "data": [json.loads(r[0]) for r in rows],
        "recordsTotal": total,
        "recordsFiltered": filtered,
    }


def _mirror_exact(term: str) -> list[dict]:
    """Rows whose number IS this term. Indexed: milliseconds, not a scan.

    Empty when the mirror is not ready, so callers fall back to search().
    """
    norms = normalise(term)
    if not norms or not mirror_ready():
        return []
    con = _mirror_db()
    try:
        rows = con.execute(
            "SELECT raw FROM mirror WHERE nafdac_norm = ?", (norms[0],)
        ).fetchall()
    finally:
        con.close()
    return [json.loads(r[0]) for r in rows]


def sync_run(page_size: int = 200, max_pages: int | None = None,
             on_progress=None) -> dict:
    """Copy the whole listing into the local mirror.

    Resumable and idempotent: every write is keyed by the registry's own
    product id, an interrupted run continues where it stopped, and rows from
    older passes are only deleted when a pass completes.
    """
    page_size = max(25, min(int(page_size or 200), 500))
    st = _sync_get()
    if sync_running():
        return {**sync_status(), "started": False,
                "note": "A sync is already running."}
    resume = st["status"] in ("error", "paused") and st["pass"] > 0
    pass_no = st["pass"] if resume else st["pass"] + 1
    # Back up one page on resume: the listing can shift under us.
    start = max(0, st["fetched"] - page_size) if resume else 0
    total = st["total"] if resume else 0
    fetched = start
    pages = 0
    _sync_put(status="running", started_at=time.time(), total=total,
              fetched=fetched, pass_no=pass_no, error="")
    try:
        while True:
            payload = _fetch("", page_size, start)
            total = payload.get("recordsTotal") or total
            rows = payload.get("data") or []
            con = _mirror_db()
            try:
                for d in rows:
                    _mirror_upsert(con, d, pass_no)
                con.commit()
            finally:
                con.close()
            fetched += len(rows)
            pages += 1
            _sync_put(fetched=fetched, total=total)
            if on_progress:
                on_progress({"fetched": fetched, "total": total,
                             "pages": pages})
            if max_pages and pages >= max_pages:
                _sync_put(status="paused",
                            error=f"Stopped after {pages} pages (cap).")
                break
            if not rows or (total and fetched >= total):
                break
            start += len(rows)
        if not max_pages or fetched >= (total or 0):
            con = _mirror_db()
            try:
                con.execute("DELETE FROM mirror WHERE pass <> ?",
                            (pass_no,))
                con.commit()
            finally:
                con.close()
            _sync_put(status="complete", finished_at=time.time(),
                      fetched=fetched, total=total, error="")
            try:
                import audit
                audit.log("sync_complete", "", f"{fetched} records")
            except Exception:
                pass
    except RegistryUnavailable as exc:
        _sync_put(status="error", fetched=fetched, total=total,
                  error=str(exc)[:300])
    return {**sync_status(), "started": True}


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

@dataclass
class Record:
    nafdac: str
    product_name: str
    manufacturer: str
    status: str
    approval_date: str
    registration_expiry: str
    composition: str
    pack_size: str
    form: str
    marketing_category: str
    category: str = ""
    product_id: Any = None

    @staticmethod
    def from_api(d: dict) -> "Record":
        return Record(
            nafdac=(d.get("NAFDAC") or "").strip(),
            product_name=clean(d.get("product_name")),
            manufacturer=clean(d.get("applicant_name")),
            status=clean(d.get("status")),
            approval_date=(d.get("approval_date") or "")[:10],
            registration_expiry=(d.get("expiry_date") or "")[:10],
            composition=clean(d.get("composition")),
            pack_size=clean(d.get("pack_size")),
            form=clean(d.get("form_name")),
            marketing_category=clean(d.get("marketing_category_id")),
            category=clean(d.get("category_name")),
            product_id=d.get("product_id"),
        )


def clean(v: Any) -> str:
    if v is None:
        return ""
    s = str(v)
    s = s.replace("&#039;", "'").replace("&amp;", "&").replace("&nbsp;", " ")
    s = s.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
    s = s.replace("#", "").replace("*", "")
    s = re.sub(r"\s*\$\s*$", "", s)          # registry footnote marker
    return re.sub(r"\s+", " ", s).strip()


def similarity(a: str, b: str) -> float:
    """0..1 token-aware similarity, forgiving of Ltd/Limited/Nig. Plc."""
    def norm(x: str) -> str:
        x = re.sub(r"[^a-z0-9 ]", " ", x.lower())
        drop = {"ltd", "limited", "plc", "nig", "nigeria", "company", "co",
                "intl", "international", "industries", "pharmaceutical",
                "pharmaceuticals", "pharma", "the", "and"}
        toks = [t for t in x.split() if t and t not in drop]
        return " ".join(toks) or x.strip()

    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------

REGISTERED = "registered"
MISMATCH = "mismatch"
INACTIVE = "inactive"
UNVERIFIABLE = "unverifiable"
NOT_FOUND = "not_found"
AMBIGUOUS = "ambiguous"


@dataclass
class Result:
    query: str
    verdict: str
    confidence: float
    message: str
    matched: Record | None = None
    candidates: list[Record] = field(default_factory=list)
    tried: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    source: str = BASE

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def lookup(
    printed_number: str,
    manufacturer_on_pack: str | None = None,
    product_name_on_pack: str | None = None,
    offline: bool = False,
) -> Result:
    """Resolve a printed NAFDAC number to a verdict with evidence."""
    candidates = normalise(printed_number)
    if not candidates:
        return Result(
            query=printed_number, verdict=UNVERIFIABLE, confidence=0.0,
            message="Could not read a NAFDAC number from this text.",
        )

    tried: list[str] = []
    sig: list[str] = []
    hits: list[Record] = []
    fallback_hits: list[Record] = []

    for idx, cand in enumerate(candidates):
        tried.append(cand)

        # A record only counts if its registered number resolves to the
        # candidate exactly. The registry search is a substring LIKE, so a
        # free-text hit can come from the product name or a longer number.
        def matches(rec: Record) -> bool:
            return cand in normalise(rec.nafdac)

        # The indexed exact read first: the common case in milliseconds.
        # Only when it finds no true hit do we pay for the substring scan
        # (which also feeds the loose fallback below).
        recs = [Record.from_api(d) for d in _mirror_exact(cand)]
        exact = [r for r in recs if matches(r)]
        if not exact:
            recs = [Record.from_api(d) for d in
                    (search(cand, length=10,
                            offline=offline).get("data") or [])]
            exact = [r for r in recs if matches(r)]
        if exact:
            hits = exact
            if idx > 0:
                sig.append(
                    f"Printed number '{printed_number}' needed repair to "
                    f"'{cand}' (OCR/punctuation), then matched the registry."
                )
            break
        if recs and not fallback_hits:
            fallback_hits = recs

    if not hits:
        payload = search(printed_number, length=5, offline=offline)
        if (payload.get("recordsFiltered") or 0) == 0 and not fallback_hits:
            hints = []
            for alt in explore_variants(printed_number):
                alt_rows = [Record.from_api(d) for d in _mirror_exact(alt)]
                if not any(alt in normalise(o.nafdac) for o in alt_rows):
                    alt_rows = [Record.from_api(d) for d in
                                (search(alt, length=3,
                                        offline=offline).get("data") or [])]
                for o in alt_rows:
                    if alt in normalise(o.nafdac):
                        hints.append(
                            f"The same tail exists as '{alt}': {o.product_name} "
                            f"({o.manufacturer}). That is a SEPARATE registration "
                            f"series, not this product. Re-read the prefix on the "
                            f"pack before drawing any conclusion."
                        )
                        break
            return Result(
                query=printed_number, verdict=NOT_FOUND, confidence=0.9,
                message="No registry record matches this number in any known format.",
                tried=tried, signals=hints + [
                    "Absence of a record is not proof of falsification; "
                    "it is a reason to escalate and preserve the pack."
                ],
            )
        return Result(
            query=printed_number, verdict=UNVERIFIABLE, confidence=0.3,
            message="The number could not be confirmed directly; the search "
                    "returned only loose text matches.",
            candidates=(fallback_hits or [])[:5], tried=tried,
            signals=["Requires a human check of the pack and the registry record."],
        )

    if len(hits) > 1:
        return Result(
            query=printed_number, verdict=AMBIGUOUS, confidence=0.4,
            message=f"{len(hits)} registry records share this number.",
            candidates=hits[:5], tried=tried,
        )

    rec = hits[0]

    if rec.status.lower() not in ("active",):
        sig.append(
            f"Registry status is '{rec.status}'. An inactive registration is a "
            "regulatory signal, not proof of falsification."
        )
        return Result(
            query=printed_number, verdict=INACTIVE, confidence=0.7,
            message=f"Registered to {rec.manufacturer}, but status is {rec.status}.",
            matched=rec, tried=tried, signals=sig,
        )

    # Identity match against what is printed on the pack.
    if manufacturer_on_pack:
        s = similarity(manufacturer_on_pack, rec.manufacturer)
        if s < 0.62:
            sig.append(
                f"Manufacturer on pack ('{manufacturer_on_pack}') does not match "
                f"the registry holder ('{rec.manufacturer}'), similarity {s:.2f}."
            )
            return Result(
                query=printed_number, verdict=MISMATCH,
                confidence=min(0.95, 0.55 + (0.62 - s)),
                message="The number is registered, but to a different company. "
                        "This is the classic copied-number pattern.",
                matched=rec, tried=tried, signals=sig,
            )
        sig.append(
            f"Manufacturer on pack matches the registry holder (similarity {s:.2f})."
        )

    if product_name_on_pack:
        s = similarity(product_name_on_pack, rec.product_name)
        if s < 0.55:
            sig.append(
                f"Product name on pack ('{product_name_on_pack}') differs from the "
                f"registered name ('{rec.product_name}'), similarity {s:.2f}."
            )

    if rec.registration_expiry:
        sig.append(f"Registration valid to {rec.registration_expiry} "
                   f"(pack-level expiry must still be checked separately).")

    return Result(
        query=printed_number, verdict=REGISTERED,
        confidence=0.9 if manufacturer_on_pack else 0.75,
        message=f"Registered to {rec.manufacturer}, status Active.",
        matched=rec, tried=tried, signals=sig,
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_result(r: Result) -> None:
    mark = {
        REGISTERED: "[OK]  ", MISMATCH: "[!!]  ", INACTIVE: "[~]   ",
        NOT_FOUND: "[??]  ", UNVERIFIABLE: "[??]  ", AMBIGUOUS: "[~]   ",
    }.get(r.verdict, "      ")
    print(f"{mark}{r.verdict.upper()}  (confidence {r.confidence:.2f})")
    print(f"      {r.message}")
    if r.matched:
        m = r.matched
        print(f"      registry : {m.nafdac}  {m.product_name}")
        print(f"      holder   : {m.manufacturer}")
        print(f"      form     : {m.form}   pack: {m.pack_size}")
        print(f"      approved : {m.approval_date}   reg. expiry: {m.registration_expiry}")
        print(f"      status   : {m.status}")
    for s in r.signals:
        print(f"      . {s}")
    if r.tried and len(r.tried) > 1:
        print(f"      tried    : {', '.join(r.tried)}")
    if r.candidates and not r.matched:
        for c in r.candidates[:5]:
            print(f"      cand     : {c.nafdac}  {c.product_name}  ({c.manufacturer})")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Vouch NAFDAC registry lookup")
    ap.add_argument("number", nargs="?", help="NAFDAC number printed on the pack")
    ap.add_argument("--name", help="search by product name")
    ap.add_argument("--manufacturer", help="manufacturer as printed on the pack")
    ap.add_argument("--product", help="product name as printed on the pack")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--offline", action="store_true", help="cache only")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--sync", action="store_true",
                    help="mirror the whole listing locally (resumable)")
    ap.add_argument("--sync-status", action="store_true",
                    help="show how fresh the local mirror is")
    ap.add_argument("--page-size", type=int, default=200,
                    help="records per sync request (25-500)")
    ap.add_argument("--max-pages", type=int, default=0,
                    help="stop after this many sync pages (0 = all)")
    a = ap.parse_args(argv)

    if a.sync_status:
        st = sync_status()
        if a.json:
            print(json.dumps(st, indent=2))
            return 0
        print(f"mirror : {st['status']}"
              + (f"  ({st['records']} records)" if st["records"] else ""))
        if st["status"] == "complete" and st["finished_at"]:
            print("updated: "
                  + time.strftime("%Y-%m-%d %H:%M",
                                   time.localtime(st["finished_at"])))
        elif st["status"] in ("running", "error", "paused"):
            print(f"progress: {st['fetched']} of {st['total'] or '?'}")
            if st["error"]:
                print(f"note   : {st['error']}")
        print(f"source : {st['source']}")
        return 0

    if a.sync:
        def tick(p):
            total = p["total"] or "?"
            print(f"  synced {p['fetched']} of {total} "
                  f"({p['pages']} pages)", flush=True)

        out = sync_run(page_size=a.page_size,
                       max_pages=a.max_pages or None, on_progress=tick)
        if not out.get("started"):
            print(out.get("note", "A sync is already running."))
            return 0
        st = sync_status()
        print(f"done: {st['status']} — {st['records']} records")
        if st["error"]:
            print(f"note: {st['error']}")
        return 0

    try:
        if a.name:
            payload = search(a.name, length=a.limit, offline=a.offline)
            recs = [Record.from_api(d) for d in (payload.get("data") or [])]
            if a.json:
                print(json.dumps(
                    {"query": a.name,
                     "recordsTotal": payload.get("recordsTotal"),
                     "recordsFiltered": payload.get("recordsFiltered"),
                     "data": [asdict(r) for r in recs]}, indent=2))
                return 0
            print(f"'{a.name}' -> {payload.get('recordsFiltered')} of "
                  f"{payload.get('recordsTotal')} registered products\n")
            for r in recs:
                print(f"  {r.nafdac:<12} {r.product_name[:44]:<44} "
                      f"{r.manufacturer[:30]:<30} {r.status}")
            return 0

        if not a.number:
            ap.print_help()
            return 2

        res = lookup(a.number, a.manufacturer, a.product, offline=a.offline)
        if a.json:
            print(json.dumps(res.to_dict(), indent=2, default=str))
        else:
            _print_result(res)
        return 0
    except RegistryUnavailable as exc:
        print(f"Registry unavailable: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
