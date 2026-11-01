# Vouch partner specification

One page for a manufacturer or PIN-verification provider connecting to Vouch.
Vouch calls you; you answer JSON. No SDK, no account on our side needed.

## Manufacturer serial check

Vouch POSTs one serial at a time:

```
POST {VOUCH_MFR_URL}                    Content-Type: application/json
Authorization: Bearer {VOUCH_MFR_KEY}   (sent only when the key is set)
User-Agent: Vouch/0.4 manufacturer-check

{"manufacturer": "ACME Ltd", "serial": "SN-1"}
```

Answer:

```json
{"verdict": "authentic | suspicious | unknown", "detail": "..."}
```

## Scratch-PIN (MAS) check

```
POST {VOUCH_MAS_URL}
{"pin": "482910472213", "shortcode": "38353"}
```

Same answer shape. `detail` over 500 characters is truncated.

## Rules that apply to both

- **HTTPS only.** Vouch refuses plain `http://` URLs, except
  `http://localhost` and `http://127.0.0.1` for your own testing.
- **15 second timeout.** Answer fast or Vouch reports you unreachable
  (honestly, as `unknown` — never as a finding against the pack).
- **Unknown verdicts only.** `unknown` means "no statement". A
  `suspicious` answer becomes one piece of evidence among many; Vouch
  never displays a partner answer as its own verdict.
- **Test without us.** Set the URL to `demo` to exercise the wiring
  end to end with canned `unknown` answers.

## SMS inbound (provider calls Vouch)

Point your SMS gateway at:

```
POST /api/sms/inbound
{"sender": "+234...", "text": "NAFDAC A11-0551", "secret": "{VOUCH_SMS_SECRET}"}
```

Vouch answers `{"reply": "Vouch: ..."}`. A wrong or missing secret
gets 403. Delivery stays with you; Vouch only writes the reply text.

## WhatsApp check-by-message

Connect a Meta WhatsApp Cloud API app:

1. Set `VOUCH_WA_VERIFY` to a random string and give the same string to
   Meta as the webhook verify token. `GET /api/whatsapp/webhook`
   answers the handshake.
2. Set `VOUCH_WA_APP_SECRET` to the Meta app secret. Every
   `POST /api/whatsapp/webhook` must carry a valid
   `X-Hub-Signature-256`; anything else gets 403.
3. Set `VOUCH_WA_TOKEN` (API token) and `VOUCH_WA_PHONE_ID` (sender
   number id) so Vouch can reply. Without both, messages are read and
   answered nowhere — the endpoint reports `sent: false`.

Send `NAFDAC <number>` or `V <barcode>` as a message; the reply uses
the same vocabulary as the SMS seam. Photos are answered with a
please-send-text note. `VOUCH_WA_TOKEN=demo` rehearses the flow
without dialing Meta.

## What Vouch never asks

Vouch never asks a partner whether a pack is "genuine" or "fake", and
never publishes a partner answer as fact. The engine in `engine.py`
weighs your answer next to the registry record, the dates, and the
pack evidence before saying anything to the person holding the phone.
