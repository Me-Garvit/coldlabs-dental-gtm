# ColdLabs GTME — dental review-management list

**Run date:** 22 November 2026 · **Total spend:** $4.51 · **Repo:** this directory

---

## 1. The custom directory: WebMD

`doctor.webmd.com` — public, national, and the only mainstream directory that lists
**individual dentists** rather than practices, which hands us decision-maker names
for free. It is not a B2B database, so it is allowed under the brief.

**Scrape method.** Crawl path is
`specialty/dentistry/{state}/{city}` → `?pagenumber=N` → provider cards. The pages
are server-rendered, so a plain HTTP crawl with realistic headers gets the full
cards — no browser needed, no credits burned. Each card yields dentist name,
credentials, specialty, street, city, state, ZIP, phone (on enhanced listings
only, ~16%) and the profile URL. Every page is cached to disk, so the crawl is
resumable and re-runs are free. Pacing is 2–5s randomised; blocks are logged and
never silently skipped.

**What it returned:** **8,295 unique dentists** across the four metros,
**1,666** of them inside our 25 target ZIPs.

**Full-scale size.** WebMD paginates at ~68 cards per page and publishes a page
count per city:

| city | pages available | dentists (reported) |
|---|---|---|
| Houston | 82 | 4,899 |
| Dallas | 57 | ~3,800 |
| Tampa | 30 | 1,892 |
| Orlando | 30 | 1,843 |

Four metros alone hold roughly **12,400 dentist listings**. Scaled nationally by
population across the ~390 US metros WebMD indexes, the directory holds on the
order of **180,000–200,000 dentist records**. That is an extrapolation from
per-city page counts, not a measured figure, and should be treated as such.

---

## 2. Geography

**Houston, Dallas (TX) and Tampa, Orlando (FL)** — 25 ZIPs, queried as
`dentist {ZIP}` rather than `dentist {city}`. A Maps search tops out around 120
results, so a city-level query silently drops most of the market; per-ZIP queries
cover each area and give the strict same-ZIP competitor rule a complete pool.

TX and FL were chosen for volume, a heavy DSO presence that properly tests the
chain filter, and Florida's free Sunbiz registry for owner lookups.

---

## 3. Tools

| job | tool | why |
|---|---|---|
| Google Maps data | **Apify** `compass/crawler-google-places` (primary) → `compass/google-maps-extractor` (fallback) | Can sort reviews by lowest rating, which the Places API cannot. The plan specified Outscraper as the second provider; no Outscraper key was issued, so the fallback runs across two independent Apify actors. The chain is data-driven, so adding Outscraper back is one line. |
| Reviews | Apify `compass/google-maps-reviews-scraper` | $0.0006/review and takes place IDs directly |
| Directory | Plain HTTP + **Camoufox** (anti-detect Firefox via Playwright) as the browser tier | WebMD server-renders, so HTTP suffices; the browser tier exists for JS-rendered pages |
| Orchestration | Python | deterministic, testable, cached |
| Judgement calls | Claude, in-session | review summaries and name-cleaning edge cases |

### A note on blocks

Camoufox is used to **render** pages, not to get past hosts that refuse us. If a
site answers 401/403/429 or shows a CAPTCHA, the fetcher raises `Blocked` and logs
it. Healthgrades — the planned fallback directory — returns 403 to automated
requests, so it was **dropped rather than worked around**, and WebMD carried the
second source alone.

The same distinction fixed a real bug: the first pass treated **HTTP 403 as "dead
site"** and rejected 121 practices for having no website, when in fact a WAF was
turning away the scraper. DNS resolution is now the authoritative "does this
domain exist" test. That single fix moved qualified practices from 198 to 355.

---

## 4. Qualification rules

Rules run in order; the **first** failure is recorded. Every rejected row keeps its
code in `data/rejected.csv`.

| rule | reject code | cut |
|---|---|---|
| outside target geography / too thin for same-ZIP competitors | `out_of_geo` | 225 |
| no website, dead DNS, parked, or social/directory profile only | `no_website` | 98 |
| DSO or chain — blocklist, **footer-text scan**, or redirect to a DSO domain | `dso_chain` | 51 |
| not a dental practice (labs, suppliers, referral services) | `not_a_practice` | 19 |
| fewer than 10 Google reviews | `low_review_count` | 15 |
| more than 3 locations | `multi_location_gt3` | 15 |
| institutional (schools, FQHCs, VA, hospital depts) | `institutional` | 6 |
| closed listing | `closed` | 2 |
| website belongs to a different practice | `domain_mismatch` | 1 |

**The footer scan earns its place.** The name blocklist would never catch *South
Tampa Dentistry* or *The Smile Design Downtown* — but their own websites say
"a brand owned by Heartland Dental LLC, which is a dental support organization".
DSOs keep the local brand; only the site text gives them away.

It also needed a guard: `© 2019 Rizzo Dental Group` on `rizzodentalgroup.com` is
the practice's **own** copyright line, not a parent DSO. Hits whose org name
matches the practice name are ignored.

**Specialists are not rejected.** Orthodontists, oral surgeons, endodontists and
periodontists stay in when independent — they live and die by reviews too — and
carry a `specialty` flag so copy can adapt.

---

## 5. Funnel

| stage | count |
|---|---|
| Google Maps raw listings | 827 |
| WebMD dentists | 8,295 |
| → rolled up into practices | 2,178 |
| **Merged practices** (after dedupe + cross-source match) | **787** |
| of which matched in both sources | 379 |
| Qualified | **355** |
| Rejected | 432 |
| **Qualified and rated under 4.0** | **4** |
| Shipped (under 4.0 + the 4.0–4.5 tier) | 20 |

**Enrichment fill:** 17/20 carry bad-review bullets · 17/20 carry 3 same-ZIP
competitors · 15/20 carry dentist names (1,580 names across 391 practices) ·
**0/20 carry LinkedIn** — practices this small simply do not have company pages,
and the blanks are real rather than padded.

---

## 6. The headline finding: the under-4.0 pool is not there

The brief asked for 50+ practices rated under 4.0. **This run produced 4.** That is
not a pipeline failure — it is what the market looks like, measured on 787 real
practices:

| rating band | share |
|---|---|
| 4.8 – 5.0 | **74.2%** |
| 4.5 – 4.79 | 15.8% |
| 4.0 – 4.49 | 7.3% |
| 3.5 – 3.99 | 1.7% |
| under 3.5 | 1.0% |

Only **2.8%** of dentists sit below 4.0, and most of those are either DSO-owned
(Greenberg, Great Expressions, Castle, Brident — all correctly cut) or have fewer
than 10 reviews. **Qualified yield is 0.93 leads per 100 places scraped.**

Reaching 50 therefore needs ~5,400 places ≈ **$21.60** of Apify credit. The account
is on the **free $5/month plan**, which caps this run at 4.

**I tested whether smarter queries fix it.** Swapping `dentist {ZIP}` for
`medicaid dentist`, `emergency dental clinic` and `affordable dentist` across 8
ZIPs returned 144 queries' worth of results, **47 new places and one marginal
lead** that fell outside the ZIP grid. Heavy overlap, no yield improvement. The
only real lever is volume, and volume costs money.

**Options, in order of fidelity to the brief:** (a) ~$25 of credit and the 50+
under-4.0 list is deliverable as specified; (b) move the threshold to under 4.5,
where yield is 3.89/100 and 50 is reachable; (c) ship the 4 and say so. This run
ships the 4 plus a clearly-labelled **4.0–4.5 tier** (16 more) so the trade-off is
visible rather than theoretical. Every row carries a `rating_tier` column.

---

## 7. How the `/dental-reviews` command works

```
/dental-reviews <input.csv> [output.csv]
```

Input is `domain, city, state, zip` and nothing else. It:

1. **Validates and normalises** the input — root domains, 5-digit ZIPs, dedupe.
2. **Builds a ZIP pool**, reusing anything already scraped and fetching only the
   ZIPs it does not hold. On our own list this cost **nothing**.
3. **Resolves each domain** to a Google listing by matching the listing's website
   root domain, falling back to brand-name search, then to the second provider.
4. **Pulls the worst reviews**, sorted lowest-first, keeps 1–2 star reviews with
   text, sorts by date and takes the 5 newest. Captures whether the owner replied.
5. **Summarises** each review as `First name + last initial - 4-5 word summary`
   (Claude's judgement, validated by script — format, length, and a check against
   carrying health details).
6. **Finds same-ZIP competitors** — rated ≥4.5, ≥25 reviews, better than the lead,
   chains and closed listings removed, ranked by a **Bayesian average** so 4.8 on
   400 reviews beats 5.0 on 30. Strict same-ZIP: never filled from nearby.

**Cost control.** The Maps listing already reports how many 1- and 2-star reviews
exist, so places are bucketed by the depth they actually need — a place with six
bad reviews gets 9 reviews fetched, not 25. That held 550 reviews to **$0.26**.

**Repeatability proof.** `examples/` holds a 17-domain **Arizona** list — a state
this project never sourced — with the command's real output: **17/17 resolved**,
10 with bad reviews, 10 with three competitors. It re-runs from cache at **$0**,
which we verified after the budget was exhausted.

---

## 8. Unit economics

| metric | value |
|---|---|
| total spend | **$4.51** |
| Maps sourcing | $4.26 (1,018 places) |
| reviews | $0.26 (550 reviews) |
| cost per place scraped | $0.0055 |
| cost per shipped lead | **$0.23** |
| cost per qualified **under-4.0** lead | **$1.13** |
| projected cost per under-4.0 lead at scale | ~$0.43 (volume amortises the pool) |

---

## 9. Decision makers

Full detail in [decision_makers.md](decision_makers.md). In short: the **owner
dentist signs**, the **office manager champions and often holds the card** under
~$500/month. Target both.

Sources in priority order — practice website team pages, the WebMD dentist names
we already hold, state business filings (FL Sunbiz is free and names the PLLC
officers), owner replies on Google reviews, state board license lookups, then paid
B2B tools last because their coverage on two-person practices is poor.

Email waterfall: scrape published addresses → pattern-and-verify →
Prospeo/AI Ark/Findymail/Hunter → generic inbox addressed to "Dr. {Lastname}".
Tier the output as verified / catch-all / generic and send catch-alls from
separate domains.

**Title filter** — include `Owner`, `Practice Owner`, `Founder`, `Principal
Dentist`, `Managing Dentist`, `President`, `Managing Partner`, plus
`Office Manager`, `Practice Manager`, `Practice Administrator`. Exclude
`Associate Dentist`, all clinical staff (`Hygienist`, `RDH`, assistants), all
front-desk and billing roles, students and faculty, vendors — and treat any
`Regional Manager` / `VP Operations` / `Director of Practice Development` as a
**red flag that a DSO slipped the chain filter**.
