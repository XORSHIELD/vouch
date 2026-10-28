# Vouch — Check it. Chase it.

Vouch is a product-verification and complaint follow-through tool for medicines,
foods, drinks, cosmetics and everyday consumables. It is built for the last
metre of consumer protection: the moment someone is standing in a shop with a
pack in their hand, and the weeks afterwards when a complaint usually dies.

It never returns a binary “fake or genuine” verdict. Every result is one of
four states — **Clear**, **Check further**, **High-risk signals** or
**Inconclusive** — with the evidence behind it, the source, and an explicit
statement of what the check does not prove.

## Run it

```powershell
cd vouch
python server.py
```

Then open:

- `http://127.0.0.1:8077/` — marketing site and documentation pages
- `http://127.0.0.1:8077/app` — the working application
- `http://127.0.0.1:8077/api/health` — service status

No API key is required. The server uses only the Python standard library plus
FastAPI/Uvicorn.

### Optional: use a model for explanations

```powershell
$env:MODEL_API_KEY = "your-key"
$env:MODEL_BASE_URL = "https://api.meta.ai/v1"   # optional
$env:MODEL_NAME = "muse-spark-1.3"               # optional
python server.py
```

Without a key, explanations are written by the deterministic `vouch-composer`
with the same evidence and the same guardrails. The app labels which provider
was used on every answer.

## The two rails

**Rail one — NAFDAC Greenbook.** Medicines, devices, vaccines, herbals and
veterinary products. The printed registration number is resolved against the
live public register, including punctuation variants and likely OCR confusions.
The engine never crosses the alpha and numeric numbering series. An `inactive`
registration is kept distinct from a falsified pack.

**Rail two — open product databases.** Food, drink, water, cosmetics and
household products. Barcodes are check-digit validated first, then looked up in
Open Food Facts, Open Beauty Facts and Open Products Facts. A barcode match
identifies a product record; it does not prove the pack in your hand is
authentic.

## What the app does

- Camera capture, photo upload, drag-and-drop, barcode entry and live
  `BarcodeDetector` scanning where the browser supports it.
- On-device OCR through the Windows OCR engine. Extracted fields are shown for
  correction before a check is run.
- Expiry parsing with end-of-month rules; best-before and use-by treated
  differently from a medicine expiry.
- Batch plausibility, label-tampering indicators, and manufacturer/product-name
  comparison against the registry record.
- Browser-local scan history with search, filters, reopen and delete.
- Cases with deadlines, a due-now queue, snooze/close, and generated seller
  demand, NAFDAC report and FCCPC complaint drafts.
- A voice agent: press the mic to ask about the result, or say “read my result”,
  “what should I do next?”, “open a case”, “show my reports” or “open the product
  guide”. It uses the browser's speech recognition and speech synthesis, always
  keeps a typed fallback, and stores no audio.
- A product guide, privacy/terms pages, a report form and demo auth flows.

## Tests

```powershell
python test_engine.py
python test_expiry.py
python test_registry.py
python smoke.py
```

The smoke suite exercises the API, the band invariants, the case lifecycle, the
queue, the photo path and the reset flow.

## Honest limits

- A registration record is not a pack. A barcode is copyable. A photo cannot
  establish chemical content or true provenance.
- If the registry or a product database is unreachable, the affected check is
  marked unverifiable. An outage is never converted into “not registered”.
- Absence of a record is a reason to escalate, not an accusation about a seller.
- OCR misreads similar characters. The app shows the extracted text and asks you
  to confirm it.
- Accounts, cloud storage, business team workspaces, multilingual UI and
  spectroscopic analysis are **not built**. Demo auth and business forms store
  data in the browser only. There is no mail server and no sales inbox.
- Voice input depends on the browser's speech service, a microphone permission
  and a connection. It can mishear local accents or product names. The spoken
  answer is only a reading of the checks on screen; the typed box gives the same
  answers when speech is blocked or unsupported.
- Vouch is independent. NAFDAC, FCCPC and the open product databases are data
  sources, not partners or endorsers.

## Layout

```
vouch/
  server.py        FastAPI app and JSON endpoints
  engine.py        checks, banding, evidence and actions
  registry.py      NAFDAC Greenbook client and normalisation
  barcode.py       barcode validation, identity scoring, product databases
  expiry.py        date parsing and expiry assessment
  vision.py        OCR extraction and fixtures
  ai.py            explanation layer, guardrails, streaming
  cases.py         case lifecycle, deadlines, queue and stats
  actions.py       seller/regulator document builders
  ui/
    index.html     the application
    marketing.html the marketing site and docs pages
  assets/          product showcase images
  samples/img/     demo pack images used by the app
```
