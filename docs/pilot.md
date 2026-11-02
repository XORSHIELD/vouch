# Two-week pilot checklist

Run Vouch with one pharmacy or market stall for two weeks before any
wider release. One phone, one seller, real customers.

## Setup (day 0)

- Server running with auto-sync (`.\start-vouch.ps1`), reachable from
  the pilot phone on the same Wi-Fi.
- `/api/health` shows `"ok": true`, mirror complete, warnings fresh.
- Staff account created for the seller (Reports tab, analytics).
- Notebook for quotes: what customers asked, in their own words.

## Daily (5 minutes)

- Note checks run, split by medicine vs food/water/drink.
- Note every "check NAPAMS" pointer: did the customer follow it?
- Note every case opened, and every outcome recorded.
- Screenshot any confusing agent reply with the pack beside it.

## Watch for

- False flags: any genuine pack steered toward a report. Each one is
  a bug — capture the number, the pack, and the reply.
- Water/food volume: if most checks are NAPAMS pointers, the portal
  reply flow (paste → read) is the feature to polish next.
- Language: which of the five the seller actually uses; note odd
  strings for native-speaker review.
- Drop-offs: customers who start a check and walk away. Ask why.

## Exit bar

- 50+ real checks, zero unresolved false flags.
- At least 3 cases followed through to an outcome.
- Seller can run a check, open a case, and find analytics alone.
- Field-test doc sections G–J all pass on the pilot phone.
