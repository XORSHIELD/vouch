# Phone field test

Run this on a real phone against a deployed server (see
`docs/deploy.md`). It replays the flows users actually hit, including
the TadalisPlus scratch-PIN regression. HTTPS (or `localhost`) is
required for the live camera and microphone; typing, uploads, and
everything else work over plain HTTP too.

Preconditions (open in the phone browser first):

- `/api/health` shows `"ok": true` and a fresh mirror.
- `/api/ai/status` shows the model enabled (keyed path).

## A. Photo flow (Check tab)

1. Photograph the FRONT of a pack. Expect the product name read back.
2. Photograph the BACK panel. Expect NAFDAC number, batch, and dates
   filled in — check them against the pack.
3. Send a blurry photo. Expect an honest unreadable note plus
   retake guidance, not a verdict.

## B. Agent scratch-PIN flow (Ask the agent)

1. Send the pack photo.
2. Say "use the scratch card pin". Expect a request for the PIN.
3. If the panel is unscratched, say "the pin is on the image".
   Expect the scratch-state question (is the silver strip off?)
   plus the close-up suggestion — not a dump, not a guess.
4. Scratch the panel and type the PIN, e.g. `here is the pin:
   2062775840753`. Expect a short acknowledgment plus the exact
   instruction, with the shortcode remembered from the chat:
   `Text 2062775840753 to 34488`.
5. Text the PIN from your own phone, paste the service reply
   word-for-word. Expect a pass/fail/unclear reading of that
   reply, quoted as the provider's statement.

## C. Voice

1. Tap the mic, say "are you hearing me?". Expect a short spoken
   answer, not a verdict dump.
2. Speak a NAFDAC number. Expect it transcribed and checked.
3. Stay silent mid-turn. Expect the turn to end and listening
   to resume on its own.

## D. Languages

Switch through English, Pidgin, Hausa, Yoruba, Igbo in Settings.
Every screen must stay fully translated. (Hausa, Yoruba, and Igbo
strings still await native-speaker review — note anything odd.)

## E. Cases

Run a high-risk demo pack, approve the case proposal, open the
case, read all three documents, make the ADR (reaction) note from
the document buttons, then close it with an outcome.

## F. Message channels (if configured)

Text a NAFDAC number to the SMS/WhatsApp number and expect the
registry answer. Send a label photo on WhatsApp and expect the
photo read or an honest note.

## G. Water and NAPAMS (Agent tab)

1. Send a bare water number, e.g. `01-3892`. Expect a finding plus a
   pointer at NAFDAC's NAPAMS portal — never a bare accusation.
2. Send `CWAY water 01-3892`. Expect review ("a reason to check,
   not a conclusion"), a Verify-on-NAPAMS action, and no finding.
3. Open registration.nafdac.gov.ng on the phone, verify the number,
   paste the portal's reply word-for-word. Expect the product details
   read back — name, maker, dates, certificate status — with the
   earlier no-record answer explicitly replaced.

## H. Forwarded lists and pack codes

1. In the Agent tab, paste a 3-line list into "Check a forwarded
   list" (one real number, one real name, one invented line).
   Expect per-line marks plus honest counts.
2. In Verify, paste `(01)06260001146105(17)281231(10)LOT42`
   into Pack code and Decode. Expect GTIN, batch, and expiry read
   back. Change one GTIN digit; expect CHECK FAILED.
3. Paste the same code to the agent. Expect the same decode.

## I. Stop button (Agent tab)

1. Send a question, then press Stop before the answer lands.
   Expect "Stopped.", the input focused, Send live again.
2. Send the follow-up immediately. Expect a normal answer; the
   stopped turn must not leak a half answer into history.

## J. Reaction notes and inspector view

1. In the agent, say "the syrup gave my son a rash". Expect care
   first, no medical advice, and the Med Safety App handoff.
2. As staff, open analytics. Expect the Checks section to count
   today's checks and top products.

## After the run

Copy any surprising agent reply plus the matching lines from
`.data/audit.log` (`agent_step_failed` entries now carry a
digit-masked output shape) and send them back for hardening.
