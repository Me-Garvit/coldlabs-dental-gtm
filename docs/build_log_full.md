# ColdLabs GTME Assignment: Build Log and Decisions

*Garvit · 23 November 2026 · Companion to the one-page write-up. The brief caps that write-up at one page, so this document carries the longer version: what I built, what I tried that failed, and why each call went the way it did.*

**Deliverables:** [the list (Google Sheet, 9 tabs)](https://docs.google.com/spreadsheets/d/1FT1biD0FgCd-jaGVqvJ46qgSwOTZbbEYQxJfKGYK5sQ/edit) · the repo with `/dental-reviews` and all scripts it calls · `docs/writeup.md` (one page) · `docs/decision_makers.md` · `docs/slack_updates.md`.

## 1. The conclusion first

The brief asks for 50 or more independent US dental practices rated under 4.0. I measured 729 qualified practices across four metros and found **eight** that clear that bar. Under-4.0 independents are roughly 1 in 100 of the market, and most of the ones that do exist are DSO-owned or carry too few reviews to be credible. This is not a scraping shortfall. It is what the market looks like, and reaching 50 at the observed yield needs about $21.60 of Apify credit against a $5 free ceiling per account.

Rather than pad the list, I did two things. I shipped the eight alongside a clearly labelled 4.0 to 4.5 tier, and I built a second segment the brief did not ask for: practices with healthy overall ratings whose *recent* reviews have turned. That segment costs $0.007 per practice to test against $1.86 per under-4.0 lead, and it fits the client's pitch better than the headline filter does. A practice sitting at 4.6 that has taken four one-star reviews in the last 90 days has a problem it can still see and still fix. A practice at 3.2 has usually stopped caring.

**Final position: 68 leads carrying review evidence.** 42 shipped against the rating filter (8 under 4.0, 34 in the 4.0 to 4.5 tier) plus 26 actively slipping from the momentum segment. QA on the shipped file: 0 failures, 2 warnings, both on company names longer than my eight-word guideline that are correct as written.

## 2. Geography, and why these four metros

Houston and Dallas in Texas, Tampa and Orlando in Florida. 25 target ZIPs, with a 50-row ZIP config driving the sweep.

Three reasons. Volume, because all four are large enough that a per-ZIP query returns a full local market rather than a thin one. DSO density, because Texas and Florida are where Heartland, Aspen, Brident and Greenberg operate hardest, which puts real pressure on the chain filter instead of letting it pass by default. And Florida's Sunbiz registry is free and names PLLC officers, which matters for the decision-maker plan in Part 6.

The query unit is the ZIP, not the city. A Google Maps search tops out near 120 results, so `dentist Houston` silently returns a fraction of Houston. `dentist 77002` returns that ZIP properly, and it gives the same-ZIP competitor rule a complete pool to rank from. That choice is the reason competitors in the output are real neighbours rather than whatever Maps felt like surfacing.

## 3. Source one: Google Maps

1,422 raw listings, 1,353 unique after dedupe on place ID and on address.

I used Apify actors rather than the official Places API for one specific reason: Places cannot sort reviews by rating. The whole deliverable depends on pulling the *worst* reviews cheaply, and an actor that accepts `reviewsSort: lowest` fetches nine reviews where the API would have needed me to pull two hundred and filter client-side. The cost difference is roughly thirty to one.

## 4. Source two: WebMD, and why that directory

`doctor.webmd.com`. Public, national, free, and not a B2B database, so it is allowed under the brief.

The reason I picked it over Yelp, Zocdoc or Healthgrades is that WebMD lists **individual dentists** rather than practices. Every other directory would have given me a second copy of data I already had from Maps. WebMD gives me the layer Maps does not have at all: names. That is the decision-maker column, sourced for free as a side effect of the second-source requirement.

**How I scraped it.** The crawl path is `specialty/dentistry/{state}/{city}`, then `?pagenumber=N`, then the provider cards on each page. The pages are server-rendered, so a plain HTTP crawl with realistic headers returns the full cards. No browser, no credits. Each card yields name, credentials, specialty, street, city, state, ZIP, phone on enhanced listings only (about 16%), and the profile URL. Every page is cached to disk, so the crawl resumes after an interruption and re-runs cost nothing. Pacing is randomised at 2 to 5 seconds, and blocks are logged rather than silently skipped.

**What it returned.** 8,295 unique dentists across the four metros, 7,776 of them with a usable street address, 1,666 inside the 25 target ZIPs. Rolled up by address, that is 2,178 distinct practice locations.

**Size at full scale.** WebMD paginates at about 68 cards per page and publishes a page count per city. Houston shows 82 pages, Dallas 57, Tampa 30, Orlando 30. Those four metros alone hold roughly 12,400 dentist listings. Scaled by population across the roughly 390 US metros WebMD indexes, the directory holds on the order of 180,000 to 200,000 dentist records. That is an extrapolation from per-city page counts, not a measured number, and should be read as one.

## 5. The architectural call I got wrong first, then fixed

My first merge treated WebMD as a *corroborator*. Every merged row was built from a Google Maps listing, and WebMD data was attached to it where the addresses matched. That design has a hard ceiling built into it: a WebMD practice with no Maps listing in my sweep can never become a lead, no matter how good it is. 994 practices fell off that way.

The fix was to invert the relationship. WebMD has an address for every dentist. An address is enough to find the Google listing directly, and once I have the listing I have the rating, the reviews and the competitors, which is the entire downstream workflow. So WebMD became a *source* of leads, not a confirmation of them.

That required a resolver. Two versions exist in the repo:

- `webmd_resolve.py`, geo-anchored. It geocodes each unmatched address with the US Census geocoder, then hands Maps a search URL anchored to those coordinates. 78% accept rate at depth 15, about $0.075 per resolved practice.
- `webmd_resolve_fast.py`, batched. It sends addresses as search terms to a much cheaper actor, 25 per run. About $0.0003 per practice, roughly 250 times cheaper.

I chose the US Census geocoder over Nominatim after testing both on the same 14 addresses. Census placed 14 of 14 within 120 metres. Nominatim placed 9 of 14 and missed one by 5.7 km, which would have anchored a Maps search in the wrong neighbourhood entirely. Census is also free and has no rate limit worth worrying about.

**The payoff.** WebMD-discovered practices convert into the shipped list at double weight: they are 17.1% of merged practices and 35.7% of what shipped. 827 practices are confirmed by both sources, and 4,216 dentist names are attached across them.

## 6. Actor selection: what I tried and what I kept

Four Apify actors, chosen by running them against each other rather than by reading their listing pages.

| **Actor** | **Job** | **Price** | **Verdict** |
|---|---|---|---|
| compass/crawler-google-places | ZIP sweep | $0.004/place | **Kept.** Sorts reviews by rating, returns the star distribution, handles start URLs. |
| compass/google-maps-extractor | Fallback sweep, domain resolution | $0.004/place | **Kept as fallback.** Independent actor, so a failure on one does not stop the run. |
| solutionssmart/fast-google-map-scraper | Address lookup | $0.0001/place | **Kept for lookups.** 50 times cheaper, returns rating and website in one call. |
| compass/google-maps-reviews-scraper | Reviews | $0.0006/review | **Kept.** Takes place IDs directly, accepts a date floor and a sort order. |

The cost comparison that drove the lookup decision: solutionssmart returned 785 places for $0.14. The compass extractor returned 902 places for $3.40. For a job that only needs "does a Google listing exist at this address, and what is its rating", the cheap actor is strictly better.

**What I tested and rejected.** solutionssmart advertises review collection at $0.12 per 1,000 reviews, which would have been five times cheaper than compass and would have collapsed my stack to one actor. I tested it twice, once in `place` mode with `allRecords` and once in `search` mode with `placesAndReviews`, both with `maxReviews: 8`, and then refetched raw with `clean=false` to check for a secondary dataset. All three returned the place row and zero review rows. The place data is correct every time, so the actor works; the review feature specifically does not emit. Cost of establishing that: $0.0003. I kept compass for reviews.

That left one problem. solutionssmart returns Google's hex CID format (`0x8640c4239a9ff22f:0xf6b46d37962b2861`), and compass expects a `ChIJ` place ID. The two actors could not talk to each other. The fix is 20 lines: convert the second half of the hex to decimal, build `https://www.google.com/maps?cid=<decimal>`, and pass that as a start URL. I verified it end to end and it returned 8 reviews for $0.005, with the canonical `ChIJ` ID on the response rows. So the bridge also does the ID conversion permanently, as a side effect of the first reviews call.

## 7. The provider layer, and why it exists

Nothing in the pipeline calls an actor directly. Everything goes through `scripts/providers.py`, which holds a chain per step: a primary actor, a fallback, and a price. Four behaviours made it worth building.

**Fallback.** The original plan named Outscraper as the second provider. No Outscraper key was issued, so the chain falls back across two independent Apify actors instead. Adding Outscraper back is one line in a dict, not a rewrite.

**Caching, keyed on the payload.** Every call is hashed on its input and written to disk. Re-running the whole pipeline costs nothing, which is what made the repeatability proof possible after the budget was gone.

**A live spend guard.** Each call declares `maxTotalChargeUsd` and the ledger records what it actually cost, so a run stops at a cap instead of discovering the overrun afterwards.

**Thread safety.** Once I moved to five concurrent runs, the ledger and the cache needed a lock. Without it, two threads writing the ledger lose entries and the spend number drifts low, which is the worst direction for it to drift.

One ordering bug here cost me real money and is worth naming. The first version checked the budget guard *before* scanning the cache. When the budget ran out, cached answers that cost nothing became unreachable, the run returned zero rows, and the zero-row result overwrote a good output file. The cache scan now runs across the whole chain before any budget check, and the writer refuses to overwrite good output with an empty result.

## 8. Qualification

Rules run in a fixed order and the first failure is the one recorded, so every rejection has exactly one reason and the counts add up. All 624 rejected rows keep their code in `data/rejected.csv`.

| **Stage** | **Count** |
|---|---|
| Google Maps raw listings | 1,422 |
| WebMD dentists (2,178 practice locations) | 8,295 |
| Merged practices after dedupe and cross-source match | **1,353** |
| Qualified | **729** |
| Rejected | 624 |
| Qualified and rated under 4.0 | **8** |

| **Reject reason** | **Count** | **Why the rule exists** |
|---|---|---|
| no_website | 217 | Brief requirement. DNS resolution is the test, not HTTP status. |
| out_of_geo | 128 | Outside the ZIP grid, or the local pool is too thin to source same-ZIP competitors. |
| dso_chain | 116 | Name blocklist, website footer scan, or a redirect to a DSO domain. |
| multi_location_gt3 | 50 | Brief requirement. |
| low_review_count | 39 | Fewer than 10 Google reviews. A 3.0 built on four reviews is noise, and the email would reference evidence that does not hold up. |
| not_a_practice | 35 | Labs, suppliers, referral services. |
| oversized_for_icp | 17 | More than 25 dentists at one address. Added mid-build, see below. |
| institutional | 16 | Dental schools, FQHCs, VA, hospital departments. They do not buy review management. |
| closed | 3 | Brief requirement. |
| domain_mismatch | 3 | The website belongs to a different practice. |

**The footer scan is the rule I would defend hardest.** A name blocklist will never catch *South Tampa Dentistry* or *The Smile Design Downtown*, because DSOs deliberately keep the local brand on the sign. Their own websites give them away: "a brand owned by Heartland Dental LLC, which is a dental support organization". Reading the site text caught 116 chains that a name list alone would have let through, and chains slipping through is the first thing the brief says it will judge on.

The scan needed a guard, though. `© 2019 Rizzo Dental Group` on `rizzodentalgroup.com` is the practice's own copyright line, not a parent company. Hits whose organisation name matches the practice's own name are now ignored.

**Specialists stay in.** Orthodontists, oral surgeons, endodontists and periodontists are kept when independent, because they live on reviews as much as general dentists do. They carry a `specialty` flag so the copy can adapt.

## 9. Bugs that changed the answer

These are in the build log because each one silently produced a *plausible* wrong number, which is the kind of failure that survives a review.

**HTTP 403 read as "dead site".** The first pass treated any non-200 response as evidence that a practice had no website and rejected 121 live practices on that basis. A 403 is a web application firewall turning away a scraper, not a missing site. DNS resolution is now the authoritative test. That single fix moved qualified practices from 198 to 355 at the time, and it is the largest correction in the build.

**Secondary categories condemning real practices.** Valley View Dental, Westpark Dental and JC Dental Care were all cut as "not a practice" for carrying *Dental laboratory* as a secondary Google category. Plenty of real practices run an in-house lab. Only the *primary* category now condemns a listing, and a bad secondary category only fires when nothing dental appears alongside it. Qualified moved 718 to 729.

**149 dentists at one address.** Investigating why a few final-list rows carried absurd dentist counts turned up addresses that were medical office towers, not practices. That produced the `oversized_for_icp` rule at a 25-dentist threshold: more than 25 dentists at a single address is not a one-to-three-location independent, whatever the listing says.

**"University" and "college" as institution signals.** University Park is a city in the Dallas area, West University is a Houston neighbourhood, and College Park is in Orlando. Matching on those tokens cut real practices for having a neighbourhood in their name. The institutional rule now matches specific phrases, not bare tokens.

**A fuzzy domain match rejecting a dentist for owning his own name.** `domain_mismatch` had a similarity-score branch that rejected Juan Carlos Barbosa DDS for having the website `juancarlosbarbosa.com`. The branch is gone. The rule now only fires when no word of the practice name appears in the domain *and* the site is not dental.

**Multi-location practices sharing one listing's data.** The enrichment step keyed resolved listings on domain alone. `bluecloudpsc.com` operates in Orlando, Dallas and Houston, so the last one processed overwrote the other two, and three rows carried one location's rating, reviews and competitors. The key is now the domain and the ZIP together.

**A per-run cap read as per-search.** `maxCrawledPlacesPerSearch` applies per run, not per start URL. Eight anchors under a cap of 24 gave the first anchor 13 places and six anchors nothing at all. The resolver now sends one anchor per run.

**Batch size against the actor timeout.** Batches of 60 addresses exceeded the 900-second timeout and all five concurrent runs returned nothing. Batches are 25, and the timeout scales with batch length.

**Address normalisation.** A word-boundary assertion before `#` never matches, so suite numbers written as `#204` were never stripped and those addresses never matched across sources. Fixing that, plus handling orphaned `Ste` tokens and a street-suffix abbreviation map, moved matched practices from 391 to 419.

**Duplicate dentist names.** "Dr. Iracaema Gomez" and "Dr. Iracema Gomez" are one person. Collapsing spelling variants above a 90 similarity threshold took the name count from 1,680 to 1,624 at the time, and the same rule now runs over all 4,216.

## 10. One thing I refused to build

I pulled in Camoufox, an anti-detect Firefox driven through Playwright, as the browser tier of the fetcher. It is there to *render* pages that need JavaScript. It is not there to get past hosts that are refusing me.

Healthgrades was my planned fallback directory and it returns 403 to automated requests. Camoufox would probably have got through. I dropped Healthgrades instead and let WebMD carry the second source alone. The fetcher reflects that policy in code: on 401, 403, 429 or a CAPTCHA it raises `Blocked` and stops, and escalation to the browser tier is off by default. A scraper that routes around a refusal is a liability on a client account, and the brief's instruction to say so when something did not work is better served by a missing directory than by a quiet workaround.

## 11. The rating filter, measured

Across all 729 qualified practices:

| **Rating band** | **Practices** | **Share** |
|---|---|---|
| 4.8 to 5.0 | 580 | 79.6% |
| 4.5 to 4.79 | 105 | 14.4% |
| 4.0 to 4.49 | 36 | 4.9% |
| 3.5 to 3.99 | 5 | 0.7% |
| under 3.5 | 3 | 0.4% |

1.1% of qualified independents sit under 4.0. Qualified yield is 0.93 leads per 100 places scraped, so 50 under-4.0 leads needs roughly 5,400 places, about $21.60 of credit against a $5 per-account ceiling.

**I tested whether better queries fix it before concluding that budget was the only lever.** Swapping `dentist {ZIP}` for `medicaid dentist`, `emergency dental clinic` and `affordable dentist` across 8 ZIPs ran 144 queries and returned 47 new places and one marginal lead, which fell outside the ZIP grid anyway. The overlap was heavy and the yield did not move. Query design is not the constraint. Volume is.

## 12. The momentum segment

This is the part of the build I would argue for in a real account review.

The client's pitch is that weak ratings lose patients to nearby clinics. A practice at 3.2 with 400 reviews has a structural rating problem it cannot fix this quarter, and the arithmetic of review averages means it mostly cannot fix it at all. A practice at 4.6 that has taken four one-star reviews in the last 90 days has a live, visible, recoverable problem, and the email writes itself.

The probe is cheap because of one actor parameter. Setting `reviewsSort: newest` with a `reviewsStartDate` floor means only reviews inside the window are fetched and billed, so testing a healthy practice for recent decline costs about $0.007 rather than the cost of pulling its full history. I probed 286 high-rated practices and found **26 actively slipping**.

Against $1.86 per qualified under-4.0 lead, that is a 265-fold difference in cost per lead, for a segment that matches the pitch more closely. Every row is labelled, so nothing in the momentum tab is passed off as meeting the brief's rating filter.

## 13. The /dental-reviews command

```
/dental-reviews <input.csv> [output.csv]
```

Input is `domain, city, state, zip` and nothing else. Extra columns are ignored. Credentials come from `APIFY_TOKEN` and `OUTSCRAPER_API_KEY` in the environment, never from the files, and either one alone is enough.

- **Validate and normalise.** Root domains, 5-digit ZIPs, dedupe.
- **Build a ZIP pool**, reusing anything already scraped and fetching only the ZIPs it does not hold.
- **Resolve each domain** to a Google listing by matching the listing's own website root domain, falling back to brand-name search, then to the second provider.
- **Pull the worst reviews**, sorted lowest-first, keep 1 and 2 star reviews that have text, sort by date and take the 5 newest. Record whether the owner replied, which is a useful signal for the opener.
- **Summarise** each review as `First name + last initial - 4-5 word summary`. This is a two-phase handshake: the script writes `<output>.pending.json` and stops, Claude writes the summaries, the script validates format, length, and that no health detail identifying a condition has been carried over, then merges them in. The judgement stays with the model and the validation stays in code.
- **Find same-ZIP competitors** rated 4.5 or better with at least 25 reviews and better than the lead, chains and closed listings removed, ranked by a **Bayesian average** so that 4.8 across 400 reviews outranks 5.0 across 30.

**Edge cases, which the brief asks about directly.** Every row gets a `status`, and a partial result is written rather than dropped:

- **Fewer than 3 bad reviews:** status `only_1_bad_reviews` or `only_2_bad_reviews`, and whatever exists is written. None at all gives `no_bad_reviews` and an empty cell. The cell is never filled with 3 and 4 star reviews to reach a count.
- **Listing not found:** status `listing_not_found`. The row stays in the output with its input columns intact and the enrichment columns blank, so the caller can see what failed rather than finding rows missing.
- **Fewer than 3 competitors:** status `only_1_competitors`, `only_2_competitors` or `no_competitors_in_zip`. **Same-ZIP is strict and never relaxed to a nearby ZIP.** A competitor the prospect has never heard of does not make the email land, and quietly widening the radius would hide that the ZIP is thin.

**Repeatability proof.** `examples/` holds a 17-domain **Arizona** list, a state this project never sourced, with the command's real output: 17 of 17 resolved, 10 with bad reviews, 10 with three competitors, and seven rows honestly marked `no_competitors_in_zip`. It re-runs from cache at $0, which I verified after the budget was exhausted.

## 14. Cost

**$14.29 billed across three Apify accounts** at the $5 free ceiling each. My own ledger attributes $12.48 of that to specific calls; the $1.81 gap is actor minimum charges and platform overhead that the per-call ledger does not see. I am reporting the account figure as the real one.

| **Actor** | **Runs** | **Attributed spend** |
|---|---|---|
| compass/crawler-google-places | 10 | $5.90 |
| compass/google-maps-extractor | 83 | $3.40 |
| compass/google-maps-reviews-scraper | 23 | $3.03 |
| solutionssmart/fast-google-map-scraper | 21 | $0.14 |

| **Metric** | **Value** |
|---|---|
| Cost per place scraped | $0.0055 |
| Cost per shipped lead (42) | $0.34 |
| Cost per qualified under-4.0 lead | $1.86 |
| Cost per momentum probe | $0.007 |
| Cost per lead across all 68 with evidence | $0.21 |

## 15. Gaps, stated plainly

The brief says blanks beat guesses, so here is what is missing and why.

**LinkedIn is empty on all 42 rows.** The column is wired and `clean_enrich.py` reads verified matches from `data/linkedin.json`, but I never implemented or ran the `site:linkedin.com/company` lookup. That is a step I did not do, not evidence that these practices lack company pages. I did not check.

**The WebMD address lookup is unfinished.** 7,776 dentists carry addresses and 2,178 practice locations roll up from them. I have run lookups on a small fraction. At the fast actor's rate the remaining 987 in-scope practices cost about $0.30 and roughly 56 minutes at five concurrent runs. This is the single highest-return unfinished item in the build: it is the cheapest step in the pipeline and it feeds the source that converts at double weight.

**12 leads carry ratings and competitors but no review bullets.** These are rows resolved through the cheap actor, which returns hex CIDs. The CID bridge is written and verified but not yet wired into the reviews path, so those rows have not had their reviews fetched.

**44 WebMD addresses were ambiguous**, all of them shared medical buildings. Their dentist names were deliberately left unattached rather than guessed onto the wrong practice. They are logged in `data/merge_report.json`.

**`out_of_geo` cut 128 rows**, some of them real practices, because the pool in their ZIP was too thin to source same-ZIP competitors. Wider ZIP coverage recovers them.

## 16. Where I would spend the next dollar

- **Finish the WebMD address lookup.** About $0.30 and an hour, against a source that converts at double weight. Nothing else in the build has that ratio.
- **Wire the CID bridge into the reviews path.** No cost, about 20 lines, and it closes the 12-lead gap and makes the cheap actor a first-class source.
- **Widen the ZIP grid before widening the metros.** The same-ZIP competitor rule is what makes the competitor column credible, and it needs depth per ZIP more than it needs more cities. 128 rows are sitting in `out_of_geo` for exactly this reason.
- **Scale the momentum segment.** At $0.007 per probe, the 685 practices rated 4.5 and above cost about $4.80 to test in full, and at the observed 9% hit rate that is roughly 60 more leads. That is a better list than the brief asked for, for less than the cost of 3 under-4.0 leads.
- **Raise the threshold, or raise the budget.** If the under-4.0 filter is non-negotiable, it needs about $25 of credit and the list is deliverable as specified. If the goal is leads that convert, the 4.0 to 4.5 tier and the momentum segment already outperform it.

## 17. Decision makers

Full detail is in `docs/decision_makers.md`. In short: the **owner dentist signs**, and the **office manager champions it and often holds the card** under about $500 a month. Target both.

Sources in priority order, cheapest and most accurate first: practice website team pages, the 4,216 WebMD dentist names already in hand, state business filings (Florida's Sunbiz is free and names PLLC officers), owner replies on Google reviews, state board license lookups, and paid B2B tools last, because their coverage of two-person practices is poor and that is the whole ICP.

Email waterfall: scrape published addresses, then pattern-and-verify, then Prospeo, AI Ark, Findymail and Hunter, then a generic practice inbox addressed to "Dr. {Lastname}". Output is tiered as verified, catch-all or generic, and catch-alls send from separate domains.

**Titles.** Include `Owner`, `Practice Owner`, `Founder`, `Principal Dentist`, `Managing Dentist`, `President`, `Managing Partner`, `Office Manager`, `Practice Manager`, `Practice Administrator`. Exclude `Associate Dentist` (they do not control spend), all clinical staff, all front-desk and billing roles, students and faculty, and vendors. One detail worth flagging: a `Regional Manager`, `VP Operations` or `Director of Practice Development` showing up at a supposedly independent practice is a signal that a DSO slipped the chain filter, so those titles route back into qualification rather than into the send.
