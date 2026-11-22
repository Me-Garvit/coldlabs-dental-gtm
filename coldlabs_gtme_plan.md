# ColdLabs GTME Assignment: Build Plan

**Client pitch:** Independent US dental practices with weak Google ratings are losing patients to better-rated clinics nearby.
**Our job:** Build a qualified, clean list of under-4.0 practices, each with their recent bad reviews, their better-rated competitors in the same ZIP, and the people who would sign off on fixing it.

---

## 1. Key decisions

| Decision | Choice | Why |
|---|---|---|
| Google Maps data | **Apify** (primary) + **Outscraper** (fallback) | Both can sort reviews by lowest rating, which the Google Places API can't (it returns 5 reviews, unfiltered). If Apify errors, runs out of quota or misses a listing, Outscraper retries the same step. The code runs with either key alone or both. It's a provider fallback, not rotation across free accounts, which would break both platforms' terms. |
| Custom directory | **WebMD** (doctor.webmd.com), scraped with Playwright browser automation | Public and national, with dentist name, practice name, address and phone. It lists individual dentists, which gives us decision-maker names for free. It isn't a B2B database, so it's allowed by the brief. |
| Where scrapers run | Apify with US residential proxies (WebMD) / Apify + Outscraper APIs (Maps) | Avoids geo-blocks from India (ADA Find-a-Dentist was already blocked) and anti-bot issues. Hosted runs are also easier for ColdLabs to reproduce. |
| Geography | **Texas + Florida metros:** Houston, Dallas, Tampa, Orlando | Large, fast-growing metros with lots of practices, so the "nearby clinic is beating you" pitch fits. Plenty of volume to clear 50 after the under-4.0 filter. Florida's Sunbiz business registry is free and lists practice officers, which helps decision-maker sourcing. Texas has heavy DSO presence, which tests the chain filter properly. |
| Orchestration | Python + Claude Code command | Python does the fetching, cleaning and rules, so it's deterministic and testable. Claude does name cleaning edge cases and complaint summaries, where judgment matters. |
| List delivery | Google Sheet (tabs below) + CSV copy in the repo | Easy for a reviewer to filter. The CSV keeps the repo self-contained. |

---

## 2. Pipeline overview

```
 ┌──────────────────────┐      ┌──────────────────────────┐
 │ 1a. Google Maps      │      │ 1b. WebMD (Playwright)   │
 │ Apify → Outscraper   │      │ dentists by city         │
 │ "dentist {ZIP}" grid │      │                          │
 └─────────┬────────────┘      └────────────┬─────────────┘
           │ raw_maps.csv                   │ raw_webmd.csv
           ▼                                ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 2. Normalize → roll up WebMD dentists into practices →   │
 │    validate every WebMD practice on Google Maps →        │
 │    dedupe across sources → tag Source                    │
 └──────────────────────────┬──────────────────────────────┘
                            ▼ merged.csv  (+ competitor_pool.csv)
 ┌─────────────────────────────────────────────────────────┐
 │ 3. Qualify (rules table below) → qualified / rejected    │
 └──────────────────────────┬──────────────────────────────┘
                            ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 4. Clean & enrich: name, root domain, LinkedIn, city,    │
 │    state, ZIP, source                                    │
 └──────────────────────────┬──────────────────────────────┘
                            ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 5. Rating filter: Google rating < 4.0                    │
 └──────────────────────────┬──────────────────────────────┘
                            ▼ domain, city, state, ZIP
 ┌─────────────────────────────────────────────────────────┐
 │ 6. /dental-reviews command: bad reviews + same-ZIP       │
 │    competitors + status (repeatable on any list)         │
 └──────────────────────────┬──────────────────────────────┘
                            ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 7. Final list (clinics + doctor names) → QA report       │
 │ 8. Hand-off to decision-maker + email lookup             │
 └─────────────────────────────────────────────────────────┘
```

---

## 3. Stage 1: Sourcing

### 1a. Google Maps (Apify → Outscraper)

- **Query grid:** "dentist {ZIP}" for every residential ZIP in the four metros, not "dentist Houston". A Maps search stops at about 120 results, so city-level queries silently miss most practices. Per-ZIP queries cover each area completely. If a single ZIP still hits the cap, split it into smaller map-area searches.
- **Fields pulled:** name, full address, city, state, ZIP, phone, website, rating, review count, business status, Google place ID, primary + secondary categories, coordinates.
- **Provider logic:** try Apify. On error, empty result or quota exhaustion, retry once, then run the same query on Outscraper. Log which provider served each row.
- **Caching:** raw responses saved per ZIP, so re-runs cost nothing.
- **Output:** `data/raw_maps.csv`. All rows are kept, and this file also serves as the **competitor pool** (see Stage 6).

### 1b. WebMD (Playwright on Apify)

- **Crawl path:** state → city → dentist listing pages → paginate. Optionally open profile pages for full practice details.
- **Fields pulled:** dentist name, credentials (DDS/DMD), specialty, practice name, street address, city, state, ZIP, phone, WebMD profile URL.
- **Scraper behavior:** US residential proxies, randomized 2–5s delays, pagination checkpoints (resumable), retries with backoff, and a hard stop if a CAPTCHA wall appears. Blocks are logged, never silently skipped.
- **Full-scale size:** taken from WebMD's state/city dentist counts and extrapolated nationally. Reported as an estimate and noted as such in the write-up.
- **Output:** `data/raw_webmd.csv`, one row per dentist.

---

## 4. Stage 2: Normalize, validate and dedupe

### Normalization

- Phone → E.164 digits only.
- Address → lowercase, standard suffixes (Street→St, Suite→Ste), suite numbers split out, ZIP to 5 digits.
- Name → lowercase, legal suffixes and punctuation removed (used only as a match key; the clean display name is made in Stage 4).

### Roll up WebMD dentists into practices

Dentists sharing **normalized phone** or **street address + ZIP** become one practice. All dentist names stay attached in a `doctors` column (used later for decision makers).

### Validate every WebMD practice on Google Maps

Yes, every WebMD practice is checked against Google Maps. WebMD data goes stale (dentists move, practices close), and Maps is where we get website, rating, reviews and business status. Without a Maps match we can't apply the rating filter or pull reviews.

| Step | How | Result |
|---|---|---|
| 1. Match against our Maps sweep | Same phone → match. Else same street number + ZIP + name similarity ≥ 0.85 | Source = **Both** |
| 2. Targeted Maps lookup (not in sweep) | Query "{practice name}, {address}" on Apify → Outscraper. Accept only if phone or address matches | Source = **WebMD**, `maps_verified = yes` |
| 3. No match after both providers | n/a | Rejected: `no_google_listing` (likely closed or moved, and can't be rated anyway) |

### Dedupe

- Within Maps: same place ID, or same phone + ZIP (duplicate listings for one office).
- Across sources: the matching above. A practice in both sources is one row with Source = Both.
- Duplicate rows go to the rejected tab with `duplicate_of = {id}`, so the merge can be audited.

**Output:** `data/merged.csv`, one row per practice.

---

## 5. Stage 3: Qualification rules

Every rejected row gets a **reject_code** and stays in the `rejected` tab. Rules run in the order below, and the first failure is recorded.

### Rules from the brief

| # | Rule | How we detect it | Reject code |
|---|---|---|---|
| B1 | DSO or chain | (a) Name or domain on the DSO blocklist (Heartland, Aspen, Pacific Dental, Smile Brands, Western Dental, Dental Care Alliance, Affordable Care, ClearChoice, Great Expressions, Mortenson / Bright Now / Castle, Coast Dental, Gentle Dental, Monarch, Familia, Tend, 42 North, Sonrava, MB2, Dental365, Mid-Atlantic Dental Partners and others). (b) **Homepage/footer text scan** for DSO phrasing: "a Heartland Dental supported practice", "supported by", "part of the ___ family", "©  ___ Dental Partners". Many DSOs keep the local brand name, so a name check alone misses them. (c) Website redirects to a known DSO domain. | `dso_chain` |
| B2 | Closed listing | Maps business status = permanently or temporarily closed, or WebMD-only with no Maps listing | `closed` / `no_google_listing` |
| B3 | Duplicate listing | Stage 2 dedupe | `duplicate` |
| B4 | No website | Website empty, **or** the "website" is only a social or directory profile (facebook.com, instagram, yelp, healthgrades, zocdoc, webmd, *.business.site), **or** the site is dead (DNS failure / 4xx / 5xx after redirects) or parked | `no_website` |
| B5 | More than 3 locations | Same root domain on more than 3 Maps listings, **or** the site's Locations page lists more than 3 addresses, **or** same phone across more than 3 listings | `multi_location_gt3` |

### Our own rules

| # | Rule | Why | Reject code |
|---|---|---|---|
| O1 | Not a dental practice | Labs, dental supply stores, denturist-only shops and non-dental businesses show up in "dentist" searches. They aren't buyers. | `not_a_practice` |
| O2 | Institutional / non-commercial | Dental schools, hospital dental departments, FQHCs and community health centers, government/VA/military clinics, mobile dental vans. They don't buy review management, and many don't compete for patients the same way. | `institutional` |
| O3 | Fewer than 10 Google reviews | A 3.2 rating on 4 reviews isn't a real reputation problem, and there won't be 3–5 bad reviews to quote. | `low_review_count` |
| O4 | Outside target geography | Maps returns some results outside the searched ZIPs/metros. | `out_of_geo` |
| O5 | Website points to a different business | The domain's site belongs to a different practice name (shared marketing templates, acquired practice). Prevents wrong-domain emails. | `domain_mismatch` |

**Not a rejection:** specialists (orthodontists, oral surgeons, etc.) stay in if they're independent, because they also live and die by reviews. They're flagged in a `specialty` column so the email copy can adapt.

The rating < 4.0 filter is a separate later step (Stage 5), so the funnel shows qualification and rating separately.

### Reporting

The write-up reports: raw total (by source) → after dedupe → qualified → rating < 4.0 → final. It also includes the top rejection reasons with counts.

---

## 6. Stage 4: Clean and enrich

| Field | Rule |
|---|---|
| **Company name** | Step 1 (rules): cut everything after ` \| `, ` - `, ` – `, `: ` when the tail contains a city/state, "best", "dentist in", "near me" or a phone. Remove legal suffixes (LLC, PLLC, PA, PC, Inc, "DDS PA"). Fix ALL CAPS to Title Case while keeping acronyms. Remove emojis and trailing "- Houston". Step 2 (Claude): only for names the rules flag as uncertain. Guardrails: output must use words from the original, max 6 words, never invent. If the name is just the dentist ("John Smith DDS"), it becomes "Dr. John Smith". Example: "Bright Smiles Family Dentistry, LLC \| Best Dentist in Austin TX" → **Bright Smiles Family Dentistry** |
| **Domain** | Follow redirects to the final URL → registered root domain via `tldextract` → lowercase, no scheme, www, path or query. Builder subdomains (*.wixsite.com, *.squarespace.com) are kept as-is and flagged `builder_domain`, since there's no root domain to email. |
| **LinkedIn URL** | Search `site:linkedin.com/company "{clean name}" {city}`. Accept **only** if the LinkedIn page's website field matches our domain, or the name + city match exactly. Otherwise **blank**. Expect a low fill rate, since most small practices have no company page. This is reported, not padded. |
| **City / State / ZIP** | From the Maps address (more current than WebMD). ZIP is 5-digit. |
| **Source** | `Google Maps` / `WebMD` / `Both` (plus `maps_verified` for WebMD-only rows) |

---

## 7. Stage 5: Rating filter

Keep practices with a **Google rating below 4.0** (from Maps, at the time of the scrape). We also store `review_count` and `scraped_at`, so the rating is traceable.

---

## 8. Stage 6: The `/dental-reviews` Claude Code command (repeatable)

**Input:** a CSV with `domain, city, state, zip`. Nothing else.
**Output:** the same CSV with review and competitor columns added.
Runs on any fresh list with no edits. Keys come from env vars `APIFY_TOKEN` and/or `OUTSCRAPER_API_KEY`, and either alone works.

### How it works

1. **Validate input:** normalize domains, check required columns, dedupe rows.
2. **Find the Google listing for each domain**
   - Search Maps for "dentist {ZIP}" (reusing the ZIP cache) and match a listing whose website root domain = input domain.
   - Not found → search by the site's `<title>`/brand name + city.
   - Still not found → retry both on the fallback provider.
   - Multiple listings with that domain → pick the one in the input ZIP, else the nearest.
3. **Pull bad reviews**
   - Fetch up to 50 reviews sorted by lowest rating. Keep 1–2 star reviews **with text**, sort by date (newest first) and take the 5 newest.
   - Also capture whether the **owner replied** to each.
4. **Summarize (Claude, in-session)**
   - Claude turns each review into `First name + last initial - 4-5 word summary`, e.g. `Sarah M. - waited two hours past appointment`.
   - Non-English reviews are summarized in English.
   - A validator script checks the format (name pattern, 4–5 words, no PHI-like detail) and sends failures back for a rewrite.
5. **Find same-ZIP competitors**
   - From the ZIP cache (one Maps search per unique ZIP, shared by every lead in that ZIP). Only results whose parsed address ZIP = the lead's ZIP.
   - Keep categories Dentist, Dental clinic, Cosmetic dentist, Family dentist. Drop orthodontist, oral surgeon, endodontist, periodontist, labs and suppliers, unless the lead has the same specialty.
   - Remove the lead itself, DSOs/chains and closed listings.
   - **Good rating:** ≥ 4.5 stars, ≥ 25 reviews, and rated higher than the lead.
   - **Ranking:** a weighted score (Bayesian average), so 4.8 on 400 reviews beats 5.0 on 30.
   - Names cleaned with the same rules as Stage 4. Top 3.
6. **Write output** and a run summary (rows, status counts, API calls, cost, runtime).

### Output columns

| Column | Example |
|---|---|
| `google_rating`, `review_count` | 3.4, 87 |
| `bad_reviews` (bullets, email-ready) | `- Sarah M. - waited two hours past appointment`<br>`- James R. - billed for work never done` |
| `bad_review_count_90d`, `last_bad_review_date` | 3, 2026-11-02 |
| `owner_replied_to_bad` | 1 of 5 |
| `competitors` (bullets) | `- Lakeside Family Dental`<br>`- Smile Studio`<br>`- Elm Street Dentistry` |
| `competitor_1..3_name / rating / reviews` | Lakeside Family Dental / 4.8 / 412 |
| `rating_gap` | +1.4 |
| `status` | `ok`, or one or more of the codes below |

### Edge cases

| Situation | Behavior | Status |
|---|---|---|
| Fewer than 3 bad reviews | Return what exists, never padded | `only_N_bad_reviews` |
| No 1–2 star reviews with text | Bad-review columns blank | `no_bad_reviews` |
| Google listing not found (after both providers) | All review/competitor columns blank, row kept | `listing_not_found` |
| Fewer than 3 qualifying competitors in the ZIP | Return what exists (strict same-ZIP, no filling from nearby) | `only_N_competitors` / `no_competitors_in_zip` |
| Domain dead or invalid | Still attempt the Maps match by domain; flagged | `domain_unreachable` |
| Provider error / rate limit | Backoff + retry → fallback provider → row-level error. The run never crashes on one row. | `provider_error` |
| Re-run after interruption | Resumes from cache, so completed rows aren't re-fetched | n/a |

---

## 9. Stage 7: Final list and QA

### Google Sheet tabs

1. **README:** what each tab and column means, run date, sources.
2. **Final List:** qualified, under 4.0, with reviews + competitors (the 50+ deliverable).
3. **Qualified (all ratings):** everyone who passed Stage 3.
4. **Rejected:** every rejected row with `reject_code` and `duplicate_of`.
5. **Funnel:** counts at each stage + rejection breakdown.
6. **Doctors:** practice ↔ dentist names (input for decision makers).
7. **QA Report:** automated checks (below).

### Automated QA (runs every time)

- No duplicate domains, place IDs or phones in the Final List.
- Every domain resolves, and no domain contains `http`, `www` or `/`.
- Chain recheck: no Final List name or domain matches the DSO blocklist.
- Name check: no `|`, `LLC`, `PLLC`, "best", city suffixes or ALL CAPS left.
- Review bullets match the `Name - summary` pattern, 4–5 words.
- Every competitor's ZIP = the lead's ZIP, rated ≥ 4.5 and higher than the lead.
- Before/after sample of name cleaning for spot review.

### Repeatability proof

Run `/dental-reviews` on **10 domains from a state we never sourced** and ship that input + output in `examples/`.

### Unit economics

Log API calls and cost per run → report **cost per qualified lead** and runtime.

---

## 10. Stage 8: Decision makers + email (Part 6, done last)

### Contact sources, in order

| # | Source | Gives us |
|---|---|---|
| 1 | Practice website: "Meet the Doctor" / "Our Team" (scraped, LLM-extracted) | Owner dentist, office manager, sometimes direct emails |
| 2 | WebMD dentist names (already attached) | Dentists at the practice |
| 3 | State business filings: FL Sunbiz (free), TX SOS | Legal officers/managers of the PLLC/PA, i.e. the actual owner |
| 4 | Owner replies on Google reviews | Often signed "Dr. Patel" or "Lisa, Office Manager" |
| 5 | State dental board license lookup | Confirms a licensed dentist at that address |
| 6 | NPI Registry / LinkedIn / AI Ark / Prospeo | Fills the gaps |

### Email waterfall

1. **Website scrape:** mailto links, contact page, footer, privacy policy. Small practices often publish `drsmith@` or a personal Gmail/AOL.
2. **Pattern + verify:** `drlastname@`, `firstname@`, `drfirstlast@`, `firstname.lastname@` → MillionVerifier / Reoon.
3. **B2B tools as a waterfall:** Prospeo → AI Ark → Findymail / LeadMagic → Hunter (Hunter crawls the web, not LinkedIn, so it's better for tiny domains).
4. **Generic inbox fallback:** `office@` / `info@` / `frontdesk@`, addressed to "Dr. {Lastname}". The office manager reads it.

### Handling poor coverage

- **Name first, email second.** Most of the coverage comes from steps 1–4, not B2B tools.
- **Catch-all tiering:** verified / catch-all + pattern / generic. Catch-alls send from separate domains at lower volume.
- **Phone as a backup channel:** every Maps listing has one.
- **Pilot:** run 10–20 practices through the waterfall and report the real hit rate per step.

### Job title filter set

**Seniority logic:** in a 1–3 location practice, the **owner dentist signs**. The **office/practice manager** usually finds, evaluates and runs reviews and patient-experience tools, and often holds the card for under $500/month. Target both: owner as decision maker, manager as champion.

**Include:**

- Owner / buyer: `Owner`, `Practice Owner`, `Founder`, `Co-Founder`, `Principal Dentist`, `Managing Dentist`, `Lead Dentist`, `President`, `CEO`, `Managing Partner`, `Partner` (with DDS/DMD).
- Dentist titles: `Dentist`, `DDS`, `DMD`, `General Dentist`, `Cosmetic Dentist`, `Orthodontist` / `Oral Surgeon` (specialty practices only). Only when the person matches the business filing owner or is the sole dentist.
- Champion: `Office Manager`, `Practice Manager`, `Practice Administrator`, `Operations Manager`, `Business Manager`, `Marketing Manager/Director` (larger independents).

**Exclude:**

- `Associate Dentist`, `Associate`: employed, doesn't own or sign.
- `Hygienist`, `RDH`, `Dental Assistant`, `EFDA`, `Sterilization`: clinical staff, no budget.
- `Receptionist`, `Front Desk`, `Scheduling`, `Patient Coordinator`, `Treatment Coordinator`, `Billing`, `Insurance Coordinator`: no authority. A front-desk contact is only used as a last-resort generic-inbox route.
- `Student`, `Resident`, `Intern`, `Faculty`, `Professor`: not at a buying practice.
- `Regional Manager`, `Area Director`, `VP Operations`, `Director of Practice Development`: DSO titles. Seeing them means a chain slipped through.
- `Lab Technician`, `Sales Rep`, `Consultant`, `Agency`: vendors, not buyers.

---

## 11. Repo structure

```
coldlabs-dental/
├── .claude/commands/dental-reviews.md   # the command (input CSV → enriched CSV)
├── scripts/
│   ├── providers.py        # Apify + Outscraper behind one interface, fallback logic
│   ├── maps_sweep.py       # Stage 1a ZIP-grid sourcing
│   ├── webmd_scraper.py    # Stage 1b Playwright crawler (runs on Apify)
│   ├── merge_dedupe.py     # Stage 2 normalize, validate on Maps, dedupe
│   ├── qualify.py          # Stage 3 rules + reject codes
│   ├── clean_enrich.py     # Stage 4 names, domains, LinkedIn
│   ├── reviews.py          # Stage 6 listing match + bad reviews
│   ├── competitors.py      # Stage 6 same-ZIP competitors
│   ├── validate_output.py  # format checks + QA report
│   └── config/ (dso_blocklist.txt, categories.yaml, zips.csv)
├── examples/ (fresh_list_input.csv, fresh_list_output.csv)
├── data/ (gitignored raw + cache)
├── .env.example            # APIFY_TOKEN=, OUTSCRAPER_API_KEY=
└── README.md               # setup, how to run, edge cases, cost
```

---

## 12. Communication plan (Slack to the account manager)

**Kickoff (day 1)**
> Starting the dental review-management list today. Plan: Google Maps (Apify, Outscraper as backup) + WebMD as the second source, across Houston, Dallas, Tampa and Orlando. I'm cutting DSOs, closed listings, no-website and 4+ location practices, then keeping under-4.0 ratings only. Every lead will carry its recent 1–2 star reviews and the top 3 better-rated practices in the same ZIP. Target is 60+ final leads so we have a buffer over 50. First sample of 20 by tomorrow EOD for a gut check on copy fit.

**Blocker (mid-task)**
> Heads up: WebMD is blocking requests from my region and throws a bot check after a few pages. I've moved the scraper to Apify with US proxies and slowed it down. It's working again, but the WebMD pull will take about 4 extra hours. Maps sourcing isn't affected, so the timeline for the sample holds. No action needed from you.

**Final handoff**
> List is done: [Sheet link]. [X] practices pass every filter (from [Y] raw, [Z] rejected; top reasons: DSO, no website, under 10 reviews). Each row has 3–5 bad reviews, owner-reply rate and 3 same-ZIP competitors with ratings. The command is in the repo and runs on any domain/city/state/ZIP list, tested on a fresh Arizona sample. Cost came to about $[N] per qualified lead. Gaps, stated honestly: LinkedIn found for [n]%, [k] rows have fewer than 3 competitors in their ZIP. Decision-maker plan is in the write-up and I can run a 20-practice email pilot next.

*(Bracketed numbers get filled in after the run.)*

---

## 13. Deliverables checklist (mapped to the brief)

- [ ] 50+ practices passing every filter, in a Google Sheet + CSV
- [ ] Clean name, root domain, LinkedIn (blank if none), city, state, ZIP, source
- [ ] Rating < 4.0, 3–5 bad review bullets, top 3 cleaned competitor names
- [ ] `/dental-reviews` command + scripts in a GitHub repo, keys in env vars only
- [ ] Edge cases documented (README + this plan)
- [ ] One-page write-up: directory, scrape method, full-scale size, geography, tools, rules, funnel counts, how the command works, contact sources, title filter
- [ ] Slack updates: kickoff, blocker, handoff
- [ ] 3–5 minute Loom

### Stand-out extras

- [ ] Owner-reply rate on bad reviews
- [ ] Competitor rating, review count and rating gap
- [ ] Bad-review recency (last 90 days, last date)
- [ ] Rejected tab with reasons + automated QA report
- [ ] Fresh-list proof run in `examples/`
- [ ] Cost per qualified lead
- [ ] One sample email written only from the columns
- [ ] Email-coverage pilot (10–20 practices, hit rate per step)

---

## 14. Volume estimate

| Stage | Expected |
|---|---|
| Raw (Maps + WebMD, after rollup) | 800–1,000 practices |
| After dedupe | ~700–850 |
| After qualification | ~400–500 |
| Rating < 4.0 | ~60–100 (assumes 15–20% under 4.0) |

If we're under 60 after the rating filter, add metros (San Antonio, Jacksonville) rather than loosening the rules.

---

## 15. Open decisions and risks

| Item | Status |
|---|---|
| Geography | Assumed **US (TX + FL)**. WebMD is US-only and the brief says US. Australia was mentioned earlier, so confirm. |
| Brief's Loom video | Not reviewed yet. Check for requirements not in the written brief. |
| Same-ZIP competitor rule | Default **strict** (no nearby fill). Confirm. |
| API keys | Need `APIFY_TOKEN` + `OUTSCRAPER_API_KEY` |
| Run location | Scrapers run on Apify / your Mac. The build sandbox can't reach Google, WebMD or Apify directly. |
| WebMD anti-bot | Mitigated with US proxies and pacing. If it stays blocked, the fallback directory is Healthgrades (same scraper pattern). |
| Maps result cap in dense ZIPs | Split into map-area tiles if a ZIP returns ~120 results |
| LinkedIn fill rate | Expected low. Blanks reported honestly. |
