# ColdLabs GTME — dental review-management lead list

Independent US dental practices with weak Google ratings are losing patients to
better-rated clinics nearby. This repo builds the list that proves it, lead by
lead: under-4.0 practices, their recent bad reviews, the better-rated practice
in the same ZIP, and the people who would sign off on fixing it.

Everything here is reproducible from an API key and `make all`.

---

## Setup

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env     # then fill in APIFY_TOKEN
```

Credentials live in `.env` / the environment only — never in code or CSVs.

| variable | required | notes |
|---|---|---|
| `APIFY_TOKEN` | yes | Google Maps sourcing and reviews |
| `OUTSCRAPER_API_KEY` | no | second provider; the code runs with either key alone |
| `RUN_BUDGET_USD` | no | hard spend ceiling, default `4.60` |

Camoufox (the browser tier) downloads a patched Firefox on first use:

```bash
./.venv/bin/python -m camoufox fetch
```

---

## Run the pipeline

```bash
make all          # or step by step:
make sweep        # 1a  Google Maps, ZIP grid
make webmd        # 1b  WebMD directory
make merge        # 2   normalise, roll up, match, dedupe
make probe        # 3a  one HTTP pass per practice site
make qualify      # 3b  rules + reject codes
make enrich       # 4   names, domains, LinkedIn
make prep         # 5   rating < 4.0 -> reviews_input.csv
make reviews      # 6   /dental-reviews on that list
make final        # 7   assemble + QA
```

Check spend headroom at any time:

```bash
./.venv/bin/python scripts/providers.py
```

---

## The repeatable command

```
/dental-reviews <input.csv> [output.csv]
```

Input is a CSV with `domain, city, state, zip` and nothing else. Output is the
same rows plus the Google rating, the recent 1–2 star reviews as one-line
summaries, and the top three better-rated practices in the same ZIP. It runs on
any fresh list with no edits — `examples/` holds an input and output from a
state this project never sourced.

Full contract, status codes and the Claude summarisation handshake:
[.claude/commands/dental-reviews.md](.claude/commands/dental-reviews.md).

---

## How the data is sourced

**Google Maps — Apify, with a provider fallback.** Queries are `dentist {ZIP}`
per ZIP, not `dentist Houston`: a Maps search tops out around 120 results, so a
city-level query silently drops most of the market. Apify's
`compass/crawler-google-places` is primary; `compass/google-maps-extractor` is
the fallback and picks up any batch the primary refuses. The plan called for
Outscraper as the second provider — no Outscraper key was issued for this run,
so the chain runs across two independent Apify actors instead. The chain is
data-driven (`CHAINS` in `scripts/providers.py`), so adding Outscraper back is a
one-line change.

**WebMD (`doctor.webmd.com`) — the custom directory.** Public, national, and it
lists *individual dentists*, which hands us decision-maker names for free. It is
not a B2B database, so it is allowed by the brief. The listing pages are
server-rendered, so a plain HTTP crawl gets the full provider cards; the browser
tier only starts when a page actually needs JavaScript.

### On browser automation and blocks

`scripts/fetcher.py` has two tiers. Tier 1 is plain HTTP. Tier 2 is
[Camoufox](https://github.com/daijro/camoufox), an anti-detect Firefox driven
through Playwright, used for two honest jobs: rendering pages whose content is
client-side (many small practice sites are Wix/Squarespace), and holding a real
browser session across a paginated crawl.

It is **not** used to get past a site that is refusing us. If a host answers
401/403/429 or shows a CAPTCHA, that is the site declining automated access, and
the fetcher raises `Blocked` and logs it rather than re-dressing the request to
look like someone else. `escalate_on_block` is `False` by default. Healthgrades
was the planned fallback directory; it returns 403 to automated requests, so it
was dropped rather than worked around, and WebMD carried the second source on
its own.

---

## Qualification rules

Rules run in order and the **first** failure is recorded. Every rejected row
keeps its `reject_code` and survives into `data/rejected.csv`, so the cuts can be
audited.

| # | rule | detection | reject code |
|---|---|---|---|
| O1 | not a dental practice | labs, supply stores, no dental category or name signal | `not_a_practice` |
| O2 | institutional | dental schools, hospital depts, FQHCs, VA, mobile vans | `institutional` |
| O4 | outside target geography | ZIP not in the swept grid | `out_of_geo` |
| B2 | closed listing | Maps permanently/temporarily closed | `closed` |
| B1 | DSO or chain | blocklist name/domain, **footer-text scan** ("a Heartland Dental supported practice"), redirect onto a DSO domain | `dso_chain` |
| B4 | no website | blank, social/directory profile only, dead DNS/4xx/5xx, or parked | `no_website` |
| B5 | more than 3 locations | >3 Maps listings on one domain or phone, or a locations page covering >3 ZIPs | `multi_location_gt3` |
| O3 | fewer than 10 reviews | a 3.2 on 4 reviews is not a reputation problem | `low_review_count` |
| O5 | domain mismatch | the site's title/brand belongs to a different practice | `domain_mismatch` |
| B3 | duplicate | Stage 2 dedupe, `duplicate_of` recorded | `duplicate` |

**Not a rejection:** specialists (orthodontists, oral surgeons, endodontists,
periodontists) stay in when they are independent — they live and die by reviews
too. They carry a `specialty` flag so the copy can adapt.

The rating < 4.0 cut is a **separate later step** (Stage 5), so the funnel shows
qualification and rating independently.

---

## Edge cases

| situation | behaviour |
|---|---|
| fewer than 3 bad reviews | return what exists, never padded (`only_N_bad_reviews`) |
| no 1–2 star reviews with text | review columns blank (`no_bad_reviews`) |
| listing not found after both providers | row kept, columns blank (`listing_not_found`) |
| fewer than 3 same-ZIP competitors | return what exists; **strict same-ZIP, never filled from nearby** |
| site-builder domain (Wix/Squarespace) | kept with the subdomain, flagged `builder_domain` — there is no root domain to email |
| WebMD address in a shared medical building | doctors attached only if the phone agrees too; otherwise logged as ambiguous and left unattached, because guessing puts the wrong dentist's name on a lead |
| WebMD dentist with no Google listing | rejected `no_google_listing` — can't be rated, likely moved or closed |
| provider error / rate limit | backoff, retry, fall back, then a row-level error. One bad row never crashes the run |
| re-run after interruption | every provider call is cached by `(actor, payload)`; completed work is never re-fetched or re-charged |
| budget exhausted mid-run | the next call refuses to start and says so, rather than draining the account |

---

## Cost control

The Apify account for this run is on the **free plan: $5.00/month total**, metered
at $0.004 per place and $0.0006 per review. Three things keep the run inside it:

1. **A hard ceiling.** Every provider call checks live account spend first and
   refuses to start once `RUN_BUDGET_USD` is reached.
2. **Caching.** Keyed on `(actor, payload)`. Re-running any stage is free.
3. **Review bucketing.** The Maps listing already says how many 1- and 2-star
   reviews a place has (`reviewsDistribution`). Sorted lowest-first, a place
   with six bad reviews needs ~9 fetched, not 25. Places are bucketed by the
   depth they actually need, one actor run per bucket.

Every call is logged to `data/spend_ledger.jsonl` with provider, run id, units
and dollars, which is where the cost-per-qualified-lead figure comes from.

---

## Repo layout

```
.claude/commands/dental-reviews.md   the command
scripts/
  providers.py        Apify behind one interface, fallback chain, spend guard
  fetcher.py          HTTP tier + Camoufox tier, and the no-circumvention rule
  maps_sweep.py       1a  ZIP-grid sourcing
  webmd_scraper.py    1b  WebMD crawler (resumable, cached per page)
  normalize.py        phone / address / name keys / domains
  merge_dedupe.py     2   rollup, cross-source match, dedupe
  site_probe.py       3a  one HTTP pass per practice site
  qualify.py          3b  rules + reject codes
  clean_enrich.py     4   name cleaning, domain, LinkedIn
  reviews.py          6   listing match + bad-review selection
  competitors.py      6   same-ZIP competitors, Bayesian ranking
  dental_reviews.py   6   the command's entry point
  final_list.py       5+7 rating filter and assembly
  validate_output.py  7   automated QA
  config/             dso_blocklist.txt, categories.json, zips.csv
examples/             a fresh-state input + output, as proof of repeatability
data/                 gitignored: raw pulls, caches, reports
```
