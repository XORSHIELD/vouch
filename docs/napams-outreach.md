# NAPAMS partner outreach

Vouch verifies medicine packs against the public Greenbook, but food,
water, and cosmetics live in NAFDAC's NAPAMS portal, which sits behind
a CAPTCHA. We point users there and read back whatever the portal told
them — we do not scrape it. The only legitimate route to automatic
food/water checks is partner access. Ask for it.

Send to: napamssupport@nafdac.gov.ng
Subject: Partner verification access for consumer safety app (Vouch)

---

Hello NAPAMS team,

I build Vouch, a free consumer tool that helps Nigerians check packs
before they buy or drink: it reads the NAFDAC number, resolves it
against the public Greenbook, and explains the result in plain words
(English, Pidgin, Hausa, Yoruba, Igbo). It never declares a product
fake or genuine — it quotes register records and names its sources.

Medicines work end to end. But food, water, and cosmetics numbers do
not appear in the Greenbook, so today Vouch can only send people to
verify manually on your portal and paste the reply back. Shoppers with
a sachet of water in hand deserve the same one-step answer.

I am asking for any partner route you offer:

1. A verification API or endpoint we may query per number, or
2. A bulk/redistributable extract of food/water/cosmetic
   registrations (number, product, holder, status), refreshed on
   any schedule you choose — we already ingest CSV or JSON shaped
   as number, product, maker, category, approved, expiry, status,
   and will map whatever columns you send, or
3. Guidance on the correct channel for this request.

What we offer in return: every food/water check cites NAPAMS as the
source, links the portal, and frames results exactly as your records
state them. We will not scrape, cache-bust, or work around the
portal's protections, with or without an agreement.

Thank you for the verification work you already publish. I am happy
to demo Vouch or meet any listing requirements.

[Your name]
[Phone number]
[Server or demo URL, if public]

---

Before sending: fill the three placeholders, send on a weekday
morning from your own name (not a noreply), and diary a one-line
follow-up after 7 days with no reply.

If they say yes to the API, the build is small: a `napams_lookup()`
beside `registry.lookup()`, cached like the mirror, consulted only
for food-kind packs and bare unknown numbers. If they say yes to
the extract, there is no build at all: save the file and run
`python lists.py extract.csv --name "<their title>" --date
<their date>`, then commit `napams_seed.json`.
