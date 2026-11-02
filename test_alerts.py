"""
NAFDAC alerts-importer tests: parse the recalls table, refresh the file.

No network: pages arrive as fixture HTML with _fetch_page stubbed.

Run:  python test_alerts.py
"""

from __future__ import annotations

import json
import os
import tempfile

TMP = tempfile.mkdtemp(prefix="vouch-alerts-")
os.environ["VOUCH_CACHE_DIR"] = os.path.join(TMP, "cache")
os.environ["VOUCH_DATA_DIR"] = os.path.join(TMP, "data")

import alerts
import safety

fails = 0

PAGE1 = """<table><tbody>
<tr><td>19-Aug-26</td><td><a href="https://nafdac.gov.ng/a043/">Public Alert
No. 043/2026-Seized BPPL Artemether/Lumefantrine</a></td><td>Safety Alert</td>
<td>Drugs</td><td>Bandaram Pharma</td></tr>
<tr><td>18-Aug-26</td><td><a href="https://nafdac.gov.ng/a038/">Public Alert
No. 038/2026 - Adulterated NITRAS, batch A11-0551 lot</a></td>
<td>Recall</td><td>Drugs</td><td>Some Maker Ltd</td></tr>
<tr><td>not a date</td><td>junk row</td></tr>
</tbody></table>"""

PAGE_EMPTY = "<html><body><p>nothing here</p></body></html>"


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("parse")
rows = alerts.parse_page(PAGE1)
check("two alert rows", len(rows) == 2, repr(rows)[:200])
check("alert number as id", rows[0]["id"] == "043/2026",
      repr(rows[0].get("id")))
check("link kept", rows[0]["url"] == "https://nafdac.gov.ng/a043/",
      repr(rows[0].get("url")))
check("maker kept", rows[0]["manufacturer"] == "Bandaram Pharma",
      repr(rows[0].get("manufacturer")))
check("published kept", rows[0]["published"] == "19-Aug-26",
      repr(rows[0].get("published")))
check("reason carries title + kind",
      "BPPL Artemether" in rows[0]["reason"]
      and "[Safety Alert]" in rows[0]["reason"],
      repr(rows[0].get("reason"))[:160])
check("dashed number lifted from title",
      rows[1]["nafdac_number"] == "A11-0551",
      repr(rows[1].get("nafdac_number")))
check("junk rows skipped", all(r["published"] for r in rows))
check("empty page parses to nothing", alerts.parse_page(PAGE_EMPTY) == [])
check("garbage parses to nothing", alerts.parse_page("<tr><td>oops") == [])

print("refresh")
_real_fetch = alerts._fetch_page
try:
    alerts._fetch_page = lambda p: PAGE1 if p == 1 else PAGE_EMPTY
    out = alerts.refresh()
    check("refresh ok", out.get("ok") is True, repr(out)[:200])
    check("two alerts filed", out.get("count") == 2, repr(out)[:200])
    with open(safety.ALERT_FILE, encoding="utf-8") as fh:
        body = json.load(fh)
    check("file shape the safety layer reads",
          isinstance(body.get("alerts"), list)
          and "NAFDAC" in body.get("source", ""), repr(body)[:160])
    found = safety.alerts(query="bppl")
    check("safety search hits the reason",
          found["configured"] is True and found["count"] == 1,
          repr(found)[:200])
    check("exact number lookup works",
          safety.alerts(nafdac="A11-0551")["count"] == 1)
    before = open(safety.ALERT_FILE, encoding="utf-8").read()

    def _boom(page):
        raise alerts.AlertFetchError("net down")
    alerts._fetch_page = _boom
    out = alerts.refresh()
    check("failed refresh keeps the old file",
          out.get("ok") is False
          and open(safety.ALERT_FILE, encoding="utf-8").read() == before,
          repr(out)[:160])
    alerts._fetch_page = lambda p: PAGE1
    out = alerts.refresh(max_pages=10)
    check("repeated pages stop the walk early",
          out.get("ok") is True and out.get("pages", 99) <= 3
          and out.get("count") == 2, repr(out)[:160])
finally:
    alerts._fetch_page = _real_fetch

print("status")
st = alerts.status()
check("status shape",
      {"configured", "count", "updated", "age_days", "stale",
       "source"} <= set(st), repr(st)[:200])
check("fresh file not stale",
      st["configured"] is True and st["stale"] is False, repr(st)[:200])
os.remove(safety.ALERT_FILE)
st = alerts.status()
check("missing file counts as stale",
      st["configured"] is False and st["stale"] is True, repr(st)[:200])

print("auto refresh")
import server as _server

_real_refresh = alerts.refresh
_calls: list = []
alerts.refresh = lambda: _calls.append(1) or {"ok": True}
try:
    out = _server._refresh_alerts()
    check("stale warnings refresh",
          out.get("ok") is True and len(_calls) == 1, repr(out)[:160])
finally:
    alerts.refresh = _real_refresh

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
