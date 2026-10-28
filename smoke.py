"""End-to-end smoke test against a running server.

    python server.py          # in one terminal
    python smoke.py           # in another

Checks every demo scenario end to end over HTTP: demo run, band, checks,
actions, case creation, documents, closing, and the guardrails.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8077"
fails = 0
passes = 0


def ok(cond: bool, label: str, detail: str = "") -> None:
    global fails, passes
    if cond:
        passes += 1
    else:
        fails += 1
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def call(path: str, method: str = "GET", body: dict | None = None,
         raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            text = r.read().decode()
            return r.status, (text if raw else json.loads(text))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


EXPECTED = {
    "genuine": "clear",
    "copied_number": "high_risk",
    "expired": "high_risk",
    "unregistered": "high_risk",
    "window": "review",
    "inactive": "review",
    "incomplete": "incomplete",
}

print("health")
status, h = call("/api/health")
ok(status == 200 and h.get("ok"), "health returns ok", str(h)[:200])
ok(h.get("registry_total", 0) > 8000,
   "registry reachable", f"total={h.get('registry_total')}")

print("\nui")
status, html = call("/", raw=True)
ok(status == 200 and "Vouch" in html, "index serves", f"status={status}")
ok("Check it. Chase it." in html, "tagline present")

print("\ndemo packs")
status, packs = call("/api/demo/packs")
keys = [p["key"] for p in packs.get("packs", [])]
ok(status == 200 and len(keys) == 7, "seven fixtures", str(keys))

bands: dict[str, str] = {}
for key in keys:
    status, out = call("/api/demo/run", "POST", {"key": key})
    if status != 200:
        ok(False, f"{key}: ran", str(out)[:300])
        continue
    a = out["assessment"]
    bands[key] = a["band"]
    want = EXPECTED.get(key)
    ok(a["band"] == want, f"{key}: band is {want}", f"got {a['band']} — {a['headline']}")
    ok(len(a["checks"]) >= 4, f"{key}: checks present", str(len(a["checks"])))
    ok(len(a["actions"]) >= 1, f"{key}: actions present")
    ok(bool(a.get("disclosure")), f"{key}: disclosure present")
    # The claim layer — headline and what we tell the person to do — must never
    # assert authenticity in either direction.
    claims = (a["headline"] + " " + " ".join(
        x["label"] + " " + x["why"] for x in a["actions"])).lower()
    ok(not any(w in claims for w in ("fake", "counterfeit", "genuine", "authentic")),
       f"{key}: no binary verdict in the claim layer", claims)

print("\nbands")
print("  " + json.dumps(bands, indent=2).replace("\n", "\n  "))

print("\nregistry truth")
status, a = call("/api/check", "POST", {"nafdac_number": "A11-0551"})
ok(a.get("registry", {}).get("nafdac") == "A11-0551", "A11-0551 resolves",
   str(a.get("registry"))[:300])
status, a = call("/api/check", "POST", {"nafdac_number": "A99-9999"})
ok(a["band"] == "high_risk", "unknown number is high risk", a["band"])
status, a = call("/api/check", "POST", {"nafdac_number": "B4-0262"})
ok("Inactive" in str(a.get("registry", {}).get("status", "")),
   "B4-0262 is inactive", str(a.get("registry"))[:200])

print("\ncase lifecycle")
call("/api/demo/reset", "POST")
status, out = call("/api/demo/run", "POST", {"key": "copied_number", "create_case": True})
ok(status == 200 and out.get("case_id"), "case created", str(out)[:200])
cid = out.get("case_id", "")
ok(cid.startswith("VC-"), "case id format", cid)

status, c = call(f"/api/cases/{cid}")
kinds = [d["kind"] for d in c.get("documents", [])]
ok(status == 200 and len(kinds) >= 3, "three documents attached", str(kinds))
ok(all(s["day"] >= 0 for s in c.get("steps", [])), "escalation has days")
ok(c["status"] == "open", "case open")

status, listing = call("/api/cases")
ok(any(x["id"] == cid for x in listing["cases"]), "case appears in list")

for kind in ("seller_demand", "nafdac_report", "fccpc_complaint"):
    status, body = call(f"/api/cases/{cid}/documents/{kind}", raw=True)
    ok(status == 200 and len(body) > 400, f"{kind} renders", f"status={status}")
    ok("NAFDAC" in body or "FCCPC" in body or "refund" in body.lower(),
       f"{kind} has substance")
    ok("not a regulator" in body.lower() or "not a legal finding" in body.lower(),
       f"{kind} carries the disclaimer")

status, c = call(f"/api/cases/{cid}/close", "POST", {"outcome": "Seller refunded in full."})
ok(c.get("status") == "closed", "case closes", str(c.get("status")))
ok(c.get("outcome") == "Seller refunded in full.", "outcome stored")

print("\ncluster")
status, cl = call("/api/cluster?nafdac=A11-0551&batch=EMZ7781")
ok(status == 200, "cluster returns", str(cl)[:200])
ok(cl.get("number_reports", {}).get("c", 0) >= 1, "number reports counted", str(cl))

print("\nchase queue")
call("/api/demo/reset", "POST")
status, out = call("/api/demo/run", "POST", {"key": "expired", "create_case": True})
cid = out.get("case_id", "")
status, q = call("/api/queue")
ok(status == 200 and len(q["queue"]) == 1, "queue lists the open case",
   str(q)[:300])
item = q["queue"][0]
ok(item["id"] == cid, "queue item is the case", str(item)[:200])
ok(item.get("step") is not None, "queue has a current step", str(item.get("step")))
ok(item["steps_left"] >= 2, "several steps remain", str(item["steps_left"]))
first_day = item["step"]["day"]
first_due = item["next_action_at"]

status, c = call(f"/api/cases/{cid}/steps/{first_day}", "POST", {"note": "sent"})
ok(status == 200, "step completes", str(c)[:200])
done = [s for s in c["steps"] if s["done"]]
ok(len(done) == 1 and done[0]["day"] == first_day, "the step is marked done",
   str([(s['day'], s['done']) for s in c['steps']]))
ok(c["next_action_at"] != first_due, "the clock advanced",
   f"{first_due} -> {c['next_action_at']}")
ok(c["current"] and c["current"]["day"] > first_day,
   "current step moved forward", str(c.get("current")))

status, c = call(f"/api/cases/{cid}/snooze?days=3", "POST")
ok(status == 200 and c["next_action_at"] >= "2026-10-28", "snooze pushes the date",
   str(c.get("next_action_at")))
ok(any(e["kind"] == "snoozed" for e in c["events"]), "snooze is on the record")

print("\nqueue empties when all steps are done")
for s in [s for s in c["steps"] if not s["done"] and s["day"] > 0]:
    call(f"/api/cases/{cid}/steps/{s['day']}", "POST", {"note": "done"})
status, c = call(f"/api/cases/{cid}")
ok(c["next_action_at"] is None, "no next action when the plan is finished",
   str(c.get("next_action_at")))
status, q = call("/api/queue")
ok(not any(x["id"] == cid and x["overdue"] for x in q["queue"]),
   "finished case is not overdue")

print("\nseeded rehearsal cases")
call("/api/demo/reset", "POST")
status, seed = call("/api/demo/seed", "POST")
ok(status == 200 and len(seed["cases"]) == 3, "three cases seeded", str(seed)[:300])
states = {("overdue" if x["overdue"] else "today" if x["due_today"] else "later")
          for x in seed["queue"]}
ok("overdue" in states, "at least one case is overdue", str(states))
ok(seed["stats"]["overdue"] >= 1, "stats count the overdue",
   str(seed["stats"]))
bad = [x for x in seed["queue"] if x["overdue"] and not x["step"]]
ok(not bad, "overdue cases still have a next step", str(bad)[:300])

print("\nphoto path")
try:
    import requests

    with open("samples/img/sample-expired.png", "rb") as fh:
        r = requests.post(BASE + "/api/extract/image",
                          files={"file": ("pack.png", fh, "image/png")},
                          timeout=180)
    ok(r.status_code == 200, "photo is read", r.text[:300])
    e = r.json()["extraction"]
    ok(e.get("nafdac_number") == "A11-0551", "photo: NAFDAC number", str(e))
    ok(e.get("batch") == "ACD1902", "photo: batch", str(e))
    ok("2026" in (e.get("exp_date") or ""), "photo: expiry", str(e))
    ok(e.get("provider") in ("windows-ocr", "fixture")
       or str(e.get("provider", "")).startswith("meta"), "photo: provider",
       str(e.get("provider")))

    fields = {k: e.get(k, "") for k in
              ("nafdac_number", "product_name", "manufacturer", "batch",
               "exp_date", "mfg_date", "strength", "pack_size")}
    fields["flags"] = ["expiry_overprint"]
    status, a = call("/api/check", "POST", fields)
    ok(a["band"] == "high_risk", "photo of an expired pack is high risk",
       f"{a['band']} — {a['headline']}")
except ImportError:
    print("  (requests not installed, skipped)")

print("\nmulti-side scan")
try:
    import requests

    def scan_post(paths, sides, barcodes=None, qr=None, rotations=None):
        handles = [open(p, "rb") for p in paths]
        try:
            return requests.post(
                BASE + "/api/scan/multi",
                files=[("files", (p.replace("/", "-"), h, "image/png"))
                       for p, h in zip(paths, handles)],
                data={
                    "sides": json.dumps(sides),
                    "barcodes": json.dumps(barcodes or [None] * len(paths)),
                    "qr": json.dumps(qr or [[] for _ in paths]),
                    "rotations": json.dumps(rotations or [0] * len(paths)),
                },
                timeout=180,
            )
        finally:
            for h in handles:
                h.close()

    one = scan_post(["samples/img/sample-expired.png"], ["Back panel"])
    ok(one.status_code == 200, "one side is read", one.text[:300])
    body = one.json()
    ok(body.get("merged", {}).get("nafdac_number") == "A11-0551",
       "multi: NAFDAC number merged", str(body.get("merged")))
    ok(body.get("merged", {}).get("batch") == "ACD1902",
       "multi: batch merged", str(body.get("merged")))
    ok(body.get("frames_read") == 1, "multi: one frame read",
       str(body.get("frames_read")))
    ok(body.get("quality", {}).get("verdict") in ("good", "fair", "poor"),
       "multi: quality verdict", str(body.get("quality")))
    ok(body.get("readers"), "multi: names the reader", str(body.get("readers")))
    ok(body.get("limits"), "multi: states the limits", str(body.get("limits")))

    two = scan_post(
        ["samples/img/sample-expired.png", "samples/img/sample-genuine.png"],
        ["Back panel", "Front panel"],
    )
    ok(two.status_code == 200, "two sides are read", two.text[:300])
    body = two.json()
    ok(body.get("frames_read") == 2, "multi: both frames read",
       str(body.get("frames_read")))
    fields = {c.get("field") for c in body.get("conflicts", [])}
    ok("batch" in fields, "multi: disagreement is reported, not hidden",
       str(fields))
    ok(body.get("merged", {}).get("batch") == "ACD1902",
       "multi: the majority read is chosen", str(body.get("merged")))

    cons = scan_post(
        ["samples/img/sample-expired.png", "samples/img/sample-genuine.png"],
        ["Back panel", "Barcode / QR"],
        barcodes=[
            {"value": "5010162000164", "format": "EAN-13", "votes": 2},
            {"value": "5010162000165", "format": "EAN-13", "votes": 1},
        ],
        qr=[[], ["https://example.com/p/5010162000164"]],
        rotations=[0, 90],
    )
    ok(cons.status_code == 200, "barcode consensus runs", cons.text[:300])
    body = cons.json()
    bar = body.get("barcode") or {}
    ok(bar.get("code") == "5010162000164", "near-miss folds to the valid code",
       str(bar))
    ok(bar.get("checksum") is True, "consensus checksum is true", str(bar))
    ok(body.get("merged", {}).get("barcode") == "5010162000164",
       "consensus barcode reaches the merged fields", str(body.get("merged")))
    ok(body.get("rotations") == [0, 90], "rotations are echoed",
       str(body.get("rotations")))

    empty = scan_post([], [])
    ok(empty.status_code >= 400, "an empty scan is refused", empty.text[:200])
except ImportError:
    print("  (requests not installed, skipped)")

print("\ntext extraction")
status, ex = call("/api/extract/text", "POST", {
    "text": "AC-Drex Tablet\nNAFDAC REG NO: A11-0551\nBATCH NO: ACD2401\n"
            "MFG 06/2025  EXP 06/2028\nManufactured by A.C. Drugs Ltd, Lagos\n"
})
e = ex.get("extraction", {})
ok(status == 200, "text extraction runs", str(ex)[:200])
ok(e.get("nafdac_number") == "A11-0551", "extracts the NAFDAC number", str(e))
ok(e.get("batch") == "ACD2401", "extracts the batch", str(e))
ok(e.get("exp_date"), "extracts the expiry", str(e))

print("\nreset")
status, r = call("/api/demo/reset", "POST")
ok(status == 200 and r["stats"]["cases"] == 0, "reset clears cases", str(r))

print(f"\n{'FAILED' if fails else 'all checks passed'} "
      f"({passes} passed, {fails} failed)")
sys.exit(1 if fails else 0)
