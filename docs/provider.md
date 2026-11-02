# Provider wiring — MAS and manufacturer seams

Vouch verifies scratch PINs and maker serials through small HTTPS
plug-ins. This page is the contract a provider must speak, copied from
`mas.py` and `manufacturer.py` — if this page and those modules ever
disagree, the modules win.

## Scratch PINs (MAS)

- `VOUCH_MAS_URL` — provider endpoint. Unset means unconfigured:
  Vouch says so and tells the person what to text instead.
  `demo` rehearses without dialing anything.
- `VOUCH_MAS_KEY` — optional bearer key, sent as
  `Authorization: Bearer <key>`.

Request (HTTPS POST, JSON):

    {"pin": "2062775840753", "shortcode": "34488"}

Reply (JSON):

    {"verdict": "authentic", "detail": "PIN valid, first use.",
     "provider": "Sproxil"}

`verdict` is one of `authentic`, `suspicious`, `unknown`; anything else
is read as `unknown`. `detail` is capped at 500 characters, the reply
at 256 KB, the call at 15 seconds. Plain `http://` URLs are refused
(except `localhost` / `127.0.0.1` for tests).

How Vouch uses the answer:

- It is quoted as the provider's statement, with the provider named —
  Vouch itself never declares a pack authentic.
- `suspicious` becomes evidence for the engine, never a displayed
  verdict.
- `unknown` (or no provider) falls back to the SMS route: text the PIN
  to the shortcode and read the reply.

Shortcodes seen on real photographed panels live in
`mas.SHORTCODE_HINTS`. They are hints only — guidance always tells the
person to go by what is printed beside their panel.

## Maker serials

- `VOUCH_MFR_URL` — partner webhook. Unset or `demo` behaves as above.
- `VOUCH_MFR_KEY` — optional bearer key.

Request (HTTPS POST, JSON):

    {"manufacturer": "ACME Ltd", "serial": "SN-1"}

Reply (JSON):

    {"verdict": "unknown", "detail": "Serial format not recognised."}

Same verdict vocabulary, same transport guards, same caps. Note the
manufacturer reply carries no `provider` echo — only `verdict` and
`detail`. `engine.assess()` folds a `suspicious` answer into its
checks when the pack carries a serial.

## Checking your wiring

Safe check (validates configuration, rehearses the demo connector,
dials nothing):

    python verify_provider.py [mas|mfr|all]

Live dial (needs a configured non-demo URL, sends an obviously-fake
identifier, never a real PIN or serial):

    python verify_provider.py [mas|mfr] --live
