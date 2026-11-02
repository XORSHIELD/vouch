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
case, read all three documents, then close it with an outcome.

## F. Message channels (if configured)

Text a NAFDAC number to the SMS/WhatsApp number and expect the
registry answer. Send a label photo on WhatsApp and expect the
photo read or an honest note.

## After the run

Copy any surprising agent reply plus the matching lines from
`.data/audit.log` (`agent_step_failed` entries now carry a
digit-masked output shape) and send them back for hardening.
