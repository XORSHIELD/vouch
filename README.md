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
pip install fastapi uvicorn httpx pillow requests   # once; requests is only for smoke.py
python server.py                 # http://127.0.0.1:8077
$env:VOUCH_PORT = "8091"         # optional: run on another port
python server.py
```

Then open:

- `http://127.0.0.1:8077/` — marketing site and documentation pages
- `http://127.0.0.1:8077/app` — the working application
- `http://127.0.0.1:8077/api/health` — service status

No API key is required. Needs Python 3.10 or newer (CI runs 3.13). The product
code is standard library plus FastAPI/Uvicorn/Pillow; the test client needs
httpx and the smoke script needs requests.

### Optional: use a model for explanations

```powershell
$env:MODEL_API_KEY = "your-key"
$env:META_BASE = "https://api.meta.ai/v1"   # optional
$env:VOUCH_MODEL = "muse-spark-1.3"         # optional
python server.py
```

The key is also accepted as `META_API_KEY`, and the vision model can be
overridden separately with `META_VISION_MODEL`. Without a key, explanations
are written by the deterministic `vouch-composer` with the same evidence and
the same guardrails. The app labels which provider was used on every answer.

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

- **Guided multi-side scanning.** The scanner captures the front, back, barcode
  and any damage as separate frames, up to eight sides. A live coach measures
  sharpness, lighting, contrast and glare before a frame is taken, the preview
  rotates in 90° steps, and an optional auto-capture counts down when the frame
  is sharp. Frames stay in the tab until you press **Read sides with AI**.
- Multi-frame reading. Each side is transcribed separately, then reconciled.
  When two sides disagree the conflict is shown with both values and the side it
  came from; it is never averaged into a single confident answer.
- Barcode and QR reading through the browser's native `BarcodeDetector` where
  available, with a bundled ZXing (Apache-2.0) fallback. A code must pass its
  check digit or be seen on more than one frame, and near-identical reads are
  folded together rather than splitting the vote.
- Photo upload and drag-and-drop for each side, plus barcode entry.
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
  keeps a typed fallback, and stores no audio. A **Test microphone** control
  measures the actual input level, so a blocked or silent mic can be told apart
  from a browser speech service that failed.
- A chat agent (Agent tab, `POST /api/agent/chat`). Send a photo or a
  question; it runs the same checks as Verify, explains in words, and
  asks before opening any case. With `MODEL_API_KEY` set it plans with
  the model, otherwise it answers deterministically — the engine owns
  every verdict either way, and case creation always needs sign-in.
- A product guide, privacy/terms pages, a report form and demo auth flows.

## Tests

```powershell
python test_engine.py
python test_expiry.py
python test_registry.py
python test_scan.py
python test_sync.py
python test_auth.py
python test_actions.py
python test_ai.py
python test_backup.py
python test_barcode.py
python test_cases.py
python test_seams.py
python test_mas.py
python test_safety.py
python test_vision.py
python test_server.py
python test_agent.py
python smoke.py
python eval.py
```

Pushes also run the suite plus a server boot check in CI
(`.github/workflows/ci.yml`).

The smoke suite exercises the API, the band invariants, the case lifecycle, the
queue, the single-photo path, the multi-side scan endpoint and the reset flow.
`test_scan.py` covers frame-quality scoring, multi-side reconciliation,
conflict reporting and barcode/QR consensus.

## Greenbook sync

Vouch can keep a local copy of the whole NAFDAC Greenbook listing, so checks
are instant and keep working offline. Until the first sync finishes, every
check reads the live register.

```powershell
python registry.py --sync           # download the listing (resumable)
python registry.py --sync-status    # how fresh the copy is
```

The copy lives in `.cache/registry.sqlite` next to the 7-day query cache.
Re-running `--sync` refreshes it; an interrupted run resumes where it stopped.
The app also syncs from **Settings → Greenbook sync**, and `/api/health`
reports the sync state. A synced record is still Greenbook data, so the result
panels name the source; sample and demo runs are labelled separately.

## Running it for real

**Phone on the same Wi-Fi.** Set `VOUCH_HOST=0.0.0.0` (and allow the
firewall prompt on a private network), find the laptop address with
`ipconfig`, and open `http://<that-address>:8077/app` on the phone. Phone
browsers only allow the camera over HTTPS, so on a phone use typed entries
and gallery uploads; stop the server when done.

**Accounts.** Checks never need sign-in; cases do. The first account
registered is the admin. For scripted deploys, set `VOUCH_ADMIN_PASSWORD`
before the first start instead. Sessions last 7 days; sign in from
**Settings → Session** with your username or email. Admins can grant the
inspector role there; inspectors read every case, consumers only their own.

**Backups.** Cases, reports, accounts and the mirror live in `.data/`,
`data/` and `.cache/`. Back them up:

```powershell
python backup.py                 # timestamped copy in backups/, keeps 7
python backup.py --restore backups/<stamp>   # verify, snapshot, restore (restart after)
```

Schedule it (Task Scheduler on Windows, cron elsewhere) before real users
depend on the server. Admins can also list runs at GET /api/backups
and restore one with POST /api/backup/restore — restart the server
afterwards either way.

**Fresh data.** Set `VOUCH_AUTO_SYNC=1` and the server refreshes the
Greenbook mirror daily by itself. Without it, sync from Settings or
`python registry.py --sync`.

**Locking down.** Set `VOUCH_ORIGINS=https://your-domain` so browsers only
accept your site; without it, browsers block cross-origin API calls while
same-origin use needs nothing.
Uploads are verified as real JPG/PNG/WebP and stripped of camera metadata
(including GPS) on arrival. Per-IP rate limits apply to every endpoint,
tightest on auth and OCR. Security events land in `.data/audit.log`.

**Partner seams.** A manufacturer serial check and an SMS/USSD inbound
endpoint exist as tested seams (`/api/manufacturer/verify`,
`/api/sms/inbound`). They answer honestly until you configure
`VOUCH_MFR_URL` (+`VOUCH_MFR_KEY`) and `VOUCH_SMS_SECRET` with real
providers.

**Scratch panels.** The camera reads scratch-to-verify PINs like any other
field: the PIN locks when two frames agree, rivals stay visible, and the
result page shows the exact PIN with the shortcode beside it, a Verify
button, and copy/SMS instructions. Verification itself needs a MAS
provider: without `VOUCH_MAS_URL` the button tells the person to text the
PIN to the shortcode instead.

**AI assist.** Every locked frame is also read in full by the vision model
(dates, batch, names, PIN, print warnings) and shown under the camera as
the AI read. Live detectors always win on codes; the model fills the gaps.
Toggle it in the scanner; with no key or no network the live detectors
carry on alone.

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
  answers when speech is blocked or unsupported. If the mic hears nothing, the
  voice panel's **Test microphone** button reports whether any audio is reaching
  the browser and names the likely Windows or browser setting.
- Vouch is independent. NAFDAC, FCCPC and the open product databases are data
  sources, not partners or endorsers.

## Layout

```
vouch/
  server.py        FastAPI app and JSON endpoints
  engine.py        checks, banding, evidence and actions
  registry.py      Greenbook client, normalisation and sync mirror
  barcode.py       barcode validation, identity scoring, product databases
  expiry.py        date parsing and expiry assessment
  scan.py          frame quality, multi-side reconciliation, barcode/QR consensus
  vision.py        OCR extraction and fixtures
  ai.py            explanation layer, guardrails, streaming
  agent.py         conversational agent, proposals, planner
  cases.py         case lifecycle, deadlines, queue and stats
  auth.py          accounts, sessions, roles
  audit.py         append-only security log
  ratelimit.py     per-IP rate limiting middleware
  backup.py        timestamped backups + verified restore
  manufacturer.py  maker serial-check seam
  mas.py           scratch-PIN verification seam
  sms.py           SMS/USSD inbound seam
  actions.py       seller/regulator document builders
  safety.py        safety reports, alerts, batch patterns
  eval.py          release gate: calibration, OCR-noise stability
  visval.py        vision validation battery (dev tool)
  ui/
    index.html     the application
    marketing.html the marketing site and docs pages
    vendor/        bundled ZXing decoder and its Apache-2.0 licence
  samples/img/     demo pack images used by the app
```
