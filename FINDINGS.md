# Vouch — Registry layer: what we measured

**Date:** Oct 27, 2026 · **Status:** working code, live data, regression-tested

This document records findings from direct testing against the NAFDAC Greenbook
registry. Every number here was measured today, not estimated. They change the
product design, so they belong in the pitch.

---

## 1. The registry is queryable, with no key and no scraping

`greenbook.nafdac.gov.ng` renders its table client-side, which is why it looked
unreachable. It is not a JavaScript-only app. The table is a **DataTables
server-side endpoint on the root URL**, and it will answer plain HTTP GETs with
JSON.

```
GET https://greenbook.nafdac.gov.ng/?draw=1&columns[0][data]=product_name
    &...&start=0&length=10&search[value]=paracetamol
```

It returns, per product: NAFDAC number, product name, applicant/manufacturer,
manufacturer country, composition, pack size, form, route, strength, ATC code,
marketing category, approval date, **registration expiry date**, and status.

**8,949 registered products.** This removes the biggest Day-0 risk: we do not
need to seed a fake local index, and we do not need to reverse-engineer an
authenticated portal. `vouch/registry.py` does this with the standard library
only, plus a 7-day SQLite cache and a 0.6 s request throttle.

---

## 2. The official numbering is two disjoint series, and they collide

This is the most important finding.

NAFDAC numbers come in an alpha form (`A4-0271`) and a numeric form (`04-0271`).
They look like two spellings of one number. **They are not.** They are separate
registration series, and the same tail is used in both.

Measured collisions:

| Alpha series | Product | Numeric series | Product |
|---|---|---|---|
| `B4-1248` | Lisinopril Tablets USP 10 mg | `04-1248` | Ranoxyl Suspension |
| `A4-6148` | Lisitab 10 mg Tablets | `04-6148` | AC-Ancillin Suspension |
| `04-4671` | Listerine Coolmint Mouthwash | `A4-4671` | Corbis 10 Tablet |
| `A4-2467` | Lizopril 10 | `04-2467` | Zentel Suspension |
| `A4-2468` | Lizopril 5 | `04-2468` | Zolamox 250 Tablet |

**5 of 41 sampled records (12.2%) had a *different product* sitting at the same
tail in the other series.**

Consequence: any implementation that "tries the other spelling" to improve
recall will, on roughly one in eight lookups, match the wrong product. If that
wrong product has a different manufacturer — and it will — the app reports
`mismatch` against a **genuine** pack. That is the mechanism by which a
verification tool starts accusing innocent products, and it is exactly the
failure mode that would have appeared live on stage.

**Design decision:** `normalise()` never converts between series. OCR repair
happens only *inside* a series (letter O read for zero, `AI1` for `A11`,
spacing, dashes, case). `explore_variants()` can describe the disjoint spelling
but is **display-only** and never feeds a verdict. Locked in by
`test_registry.py`.

---

## 3. A third of the registry is not active

Sampled 777 distinct records across random offsets:

| Status | Share |
|---|---|
| Active | 66.9% |
| Inactive | **33.1%** |

And registration expiry is a separate clock. Example: all three Coartem
registrations are `Inactive`, with the last one expiring **2026-03-28**. A
genuine, sealed, in-date pack of a real Novartis product can therefore still sit
outside a current registration.

**Design decision:** `inactive` is its own verdict state, never folded into
"fake". It is a regulatory signal: stop, check the pack, ask the pharmacist.

---

## 4. Number formats are inconsistent in the registry itself

| Format | Share of sample |
|---|---|
| `A4-1234` (alpha, 4-digit) | 53.3% |
| `A4-123456` (alpha, 6-digit) | 33.7% |
| `04-1234` (numeric) | 12.5% |
| irregular | 0.5% |

Plus real irregularities in the data: `A3-100882` (six digits), `03-0634`
(leading zero), stray `$`, `#`, `*` and HTML entities in names, and at least one
number stored without its prefix (`04-3275` for Coartem).

**Design decision:** never treat a failed exact match as evidence of
falsification. Three candidate repairs, then `not_found` with an escalation
hint — never a verdict.

---

## 5. A check nobody else is doing: registration-window violation

Because the registry exposes the registration's own expiry date, Vouch can
compare it with the expiry printed on the pack:

> A pack whose stated expiry falls **after** the registration lapsed is claiming
> legal shelf life the product no longer has.

That is a computable, defensible signal built from two public facts. It does not
exist in Scan 2 Verify, the Greenbook UI, or the SMS service. It is the kind of
check that produces a real finding on a real pack in the demo.

---

## 6. What now works

```
python registry.py A11-0551                  -> registered   (A.C. Drugs Ltd, Active)
python registry.py O3-0634                   -> registered   (OCR repair to 03-0634)
python registry.py A99-9999                  -> not_found    (+ escalation hint)
python registry.py B4-0262                   -> inactive     (Novartis, lapsed)
python registry.py A11-0551 --manufacturer "Emzor Pharmaceutical Industries Ltd"
                                             -> mismatch     (copied-number pattern)
python registry.py --name coartem            -> brand search
python test_registry.py                      -> 21 checks, 0 failures
python measure.py                            -> the numbers above
```

Every verdict carries: the evidence record, the signals that produced it, the
candidate forms tried, and a confidence. No output ever says "fake" or
"genuine".

---

## 7. Still unresolved

- **Pack-level expiry** is a vision problem, not a registry problem. The registry
  tells us the product's legal window; only OCR of the actual pack tells us
  whether *this* unit is expired or re-stamped. Not started.
- **Batch numbers** are not in the registry at all. Batch clustering will come
  from our own reports, which is the point — it is data we generate, not data we
  borrow.
- **Manufacturer addresses** are sparse (`null` for many applicants); the detail
  page carries manufacturer name and country, which is enough for the identity
  match but not for a full address comparison.
- **Rate limits** are unknown. We throttle and cache; if we ever pre-index, do it
  once, gently, and cite the source.
