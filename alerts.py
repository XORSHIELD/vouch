"""
Vouch — NAFDAC recalls & alerts importer.

NAFDAC publishes recalls, safety alerts and blacklists as a table at
nafdac.gov.ng/category/recalls-and-alerts/. This module copies that
table into data/alerts.json — the file the safety layer already reads —
so official warnings answer checks without any code touching the engine.

Polite by design: page cache with a 7-day life, 1.5 s between live
requests, and a bounded page walk. Parsing is strict: only table rows
shaped like (date, title, type, product, maker) are kept; anything the
theme changes into an unfamiliar shape is skipped, never guessed.
"""

from __future__ import annotations

import html.parser
import json
import os
import re
import time
import urllib.error
import urllib.request

import safety

BASE = "https://nafdac.gov.ng/category/recalls-and-alerts/"
UA = ("Vouch/0.4 (consumer product verification research; "
      "contact: vouch-demo@example.com)")
MIN_INTERVAL = 1.5
PAGE_TTL = 7 * 24 * 3600
MAX_PAGES = 20
TIMEOUT = 30

CACHE_DIR = (os.environ.get("VOUCH_CACHE_DIR")
             or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             ".cache"))
PAGE_DIR = os.path.join(CACHE_DIR, "alert_pages")

_DATE = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{2,4}$")
_ALERT_NO = re.compile(r"(?:Public\s+)?Alert\s+No\.\s*([0-9/]+)",
                       re.IGNORECASE)
_NAFDAC_NO = re.compile(r"\b[A-Z]{0,2}\d{1,2}-\d{4,6}\b")

_last_call = [0.0]


def _throttle() -> None:
    gap = time.time() - _last_call[0]
    if gap < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - gap)
    _last_call[0] = time.time()


def _page_cache(page: int) -> str:
    os.makedirs(PAGE_DIR, exist_ok=True)
    return os.path.join(PAGE_DIR, f"page-{page}.html")


def _fetch_page(page: int) -> str | None:
    """One category page of HTML. None past the last page."""
    cache = _page_cache(page)
    try:
        if time.time() - os.path.getmtime(cache) < PAGE_TTL:
            with open(cache, encoding="utf-8") as fh:
                return fh.read()
    except OSError:
        pass
    url = BASE if page <= 1 else f"{BASE}page/{page}/"
    _throttle()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(4 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise AlertFetchError(str(exc)) from exc
    except Exception as exc:
        raise AlertFetchError(str(exc)) from exc
    if len(raw) > 4 * 1024 * 1024:
        raise AlertFetchError("alerts page too large")
    text = raw.decode("utf-8", "replace")
    try:
        with open(cache, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        pass
    return text


class AlertFetchError(RuntimeError):
    pass


class _Table(html.parser.HTMLParser):
    """Table rows as cell lists; links kept as (text, href)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list] = []
        self._row: list | None = None
        self._cell: list[str] | None = None
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._href = ""
        elif tag == "a" and self._cell is not None:
            for key, value in attrs:
                if key == "href":
                    self._href = value

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None \
                and self._cell is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append((text, self._href) if self._href else text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _cell_text(cell) -> str:
    return cell[0] if isinstance(cell, tuple) else str(cell)


def _cell_href(cell) -> str:
    return cell[1] if isinstance(cell, tuple) else ""


def parse_page(html: str) -> list[dict]:
    """Alert records from one category page. Skips unfamiliar rows."""
    parser = _Table()
    try:
        parser.feed(html or "")
    except Exception:
        return []
    out = []
    for row in parser.rows:
        if len(row) < 4 or not _DATE.match(_cell_text(row[0]).strip()):
            continue
        date, title, kind = (_cell_text(c) for c in row[:3])
        maker = _cell_text(row[4]) if len(row) > 4 else ""
        url = _cell_href(row[1])
        if not url and len(row) > 5:
            url = _cell_href(row[5])
        title = title.strip()
        if not title:
            continue
        alert_no = _ALERT_NO.search(title)
        numbers = sorted(set(_NAFDAC_NO.findall(title.upper())))
        out.append({
            "id": alert_no.group(1) if alert_no else "",
            "product_name": "",
            "nafdac_number": numbers[0] if numbers else "",
            "batch": "",
            "barcode": "",
            "manufacturer": maker.strip(),
            "reason": f"{title} [{kind.strip()}]".strip(),
            "published": date.strip(),
            "url": url,
            "source": "NAFDAC recalls & safety alerts",
        })
    return out


def refresh(max_pages: int = MAX_PAGES) -> dict:
    """Re-copy the listing into data/alerts.json. Keeps the old file on
    failure, so a bad fetch never deletes working warnings."""
    seen: dict[str, dict] = {}
    pages = 0
    repeats = 0
    try:
        for page in range(1, max(1, max_pages) + 1):
            html = _fetch_page(page)
            pages += 1
            if html is None:
                break
            rows = parse_page(html)
            if not rows:
                break
            fresh = 0
            for rec in rows:
                key = rec["url"] or rec["id"] or rec["reason"]
                if key not in seen:
                    fresh += 1
                seen[key] = rec
            # Some archives repeat the whole table on every page URL.
            repeats = repeats + 1 if not fresh else 0
            if repeats >= 2:
                break
    except AlertFetchError as exc:
        return {**status(), "ok": False, "error": str(exc)[:200]}
    if not seen:
        return {**status(), "ok": False,
                "error": "No alert rows parsed; kept the previous file."}
    body = {"source": "NAFDAC recalls & safety alerts",
            "updated": time.strftime("%Y-%m-%d"),
            "alerts": list(seen.values())}
    try:
        os.makedirs(os.path.dirname(safety.ALERT_FILE), exist_ok=True)
        with open(safety.ALERT_FILE, "w", encoding="utf-8") as fh:
            json.dump(body, fh, ensure_ascii=False, indent=1)
    except OSError as exc:
        return {**status(), "ok": False, "error": str(exc)[:200]}
    return {**status(), "ok": True, "pages": pages}


def status() -> dict:
    """How fresh the imported alerts are. Cheap local reads only."""
    try:
        found = safety.alerts()
    except Exception:
        found = {}
    recs = found.get("alerts", []) if found.get("configured") else []
    try:
        mtime = os.path.getmtime(safety.ALERT_FILE)
    except OSError:
        mtime = 0
    age = round((time.time() - mtime) / 86400, 1) if mtime else None
    return {"configured": bool(found.get("configured")),
            "count": found.get("count", len(recs)),
            "updated": found.get("updated", ""),
            "age_days": age,
            "stale": (not mtime) or (age or 0) > 7,
            "source": BASE}
