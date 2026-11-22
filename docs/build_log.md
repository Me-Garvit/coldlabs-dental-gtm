# ColdLabs GTME: How I Built It


Garvit · 23 Nov 2026 · An explainer of the approach and the technical calls. The numbers-only version is the one-page write-up; the deliverables are the Google Sheet, the repo, and the /dental-reviews command.


## 1. How I framed the problem


The client sells review management, so for the email to land every lead needs three things at once: a weak Google rating, named evidence of why it is weak, and a better-rated practice close enough that the prospect recognises the name. The unit of work is therefore not "a company record" but **a Google listing, plus its review tail, plus its ZIP neighbours**. That framing decided most of what follows:

- **Query by ZIP, not by city.** A Maps search caps out near 120 results, so a city query silently drops the market. Per-ZIP queries also leave a complete local pool to pick competitors from
- **Dedupe on place ID, not on name.** Names are marketing copy; place IDs are identity
- **The review fetch is the expensive step.** Everything upstream exists to decide which listings deserve one

## 2. The shape of the pipeline


**Source → Merge → Qualify → Enrich → Validate.** Every stage reads a CSV and writes a CSV, and every external call goes through one provider layer. Because each stage is a file on disk, any stage re-runs without the ones before it: fixing a qualification rule means re-running one script over the merged CSV. That is what makes this repeatable on a fresh list rather than a one-off.


## 3. The fetching tier: deciding when a browser is justified


There are three ways to get a page, each more expensive and more fragile than the last: plain HTTP, a headless browser, an anti-detect browser. I wrote the fetcher around one rule.


**Escalate for a technical reason, never for a permission reason.** If a page needs JavaScript to render its content, that is technical, so climb the ladder. If the host answers 401, 403, 429 or shows a CAPTCHA, that is the site refusing, and no amount of tooling makes that a yes.


### Why Camoufox specifically

- It is a **patched Firefox driven through Playwright**, not a plugin layered on top of Chrome
- The spoofing happens **inside the browser engine, at the C++ level**, rather than by injecting JavaScript that overwrites navigator properties
- That is the whole difference. Stealth plugins get caught because the injected values disagree with how the engine actually behaves. Patch the engine and there is nothing to disagree with, which makes it the right tool for **rendering pages that fight ordinary automation**

### Where I refused to use it


Healthgrades was my planned fallback directory. It returns 403 to automated requests. Camoufox would very likely have got through, and I did not point it there.

- I dropped the directory and let WebMD carry the second source alone
- The policy is enforced in code, not just stated: **escalate_on_block is False by default**, and a **Blocked** exception is terminal rather than a trigger to try harder
- Reasoning: on a real client account, a scraper that routes around a refusal is a liability. A missing directory is a line in the write-up. A cease-and-desist is not

### The payoff of checking before escalating


WebMD turned out to be server-rendered. The entire 8,295-dentist crawl therefore ran on plain HTTP with disk caching and 2 to 5 second pacing. No browser, no proxy spend, and the crawl resumes after an interruption because every page is cached.


The same technical-versus-permission distinction fixed the biggest bug in the build. My first qualification pass read **HTTP 403 as "this practice has no website"** and rejected 121 live practices. A 403 is a firewall turning away a scraper, not a missing site. DNS resolution is now the authoritative test, and qualified practices moved from 198 to 355.


## 4. Apify actors: renting scrapers instead of writing them


Google Maps actively fights scraping, and keeping a Maps scraper alive is a permanent maintenance job. Apify actors are hosted scrapers billed per result, so what I am actually renting is **the maintenance**, not the code. That is the right trade for a list-building project.


But an actor is a vendor, and vendors break, change their schema, go down and reprice. So nothing in the pipeline calls an actor directly.


### The provider layer

- **A chain per step.** Each step names a primary actor, a fallback, and a price. Swapping Outscraper back in is one line in a dict
- **Payload-keyed cache.** Every call is hashed on its input and written to disk, so a re-run of the whole pipeline costs $0. This is what let me prove repeatability after the budget was gone
- **Live spend guard.** Each call declares maxTotalChargeUsd and a ledger records what it really cost, so a run stops at a cap instead of discovering the overrun afterwards
- **Thread-safe writes.** Once I moved to five concurrent runs, two threads appending to the ledger would lose entries and under-report spend, which is the worst direction for that number to drift

### Choosing actors by testing them against each other

1. **compass/crawler-google-places** for the sweeps. It is the only one that sorts reviews by rating and returns the star distribution
1. **That distribution is a cost lever.** It tells me how many 1 and 2 star reviews exist before I pay to fetch any, so each place is bucketed to the depth it actually needs. A place with six bad reviews gets nine fetched, not twenty-five. 550 reviews cost $0.26
1. **solutionssmart/fast-google-map-scraper** for address lookups, at $0.0001 per place against $0.004. For a job that only asks "is there a listing here, and what is its rating", 50x cheaper is simply better
1. **Its review mode is advertised but inert.** I tested two modes plus a raw fetch; all returned the place row and zero reviews. Establishing that cost $0.0003, and compass kept the reviews job

### The ID bridge, and why it was worth 20 lines


The cheap actor returns Google’s hex CID format and compass expects a ChIJ place ID, so the two could not talk to each other. Converting the second half of the hex to decimal and building a maps?cid= URL solves it.

- Verified end to end: 8 reviews returned for $0.005
- Better than expected: the response rows carry the canonical ChIJ ID, so the first reviews call **converts the ID permanently** as a side effect
- Gotcha found the hard way: maxCrawledPlacesPerSearch applies **per run, not per start URL**. Eight anchors under a cap of 24 gave the first anchor 13 places and six anchors nothing

## 5. Claude Code: splitting judgement from determinism


The principle I built to: **code does what is deterministic and auditable, the model does what needs judgement, and neither does the other’s job.**

- **Code owns:** fetching, caching, spend, dedupe, rule evaluation, validation, output
- **The model owns:** summarising a review into 4 to 5 words, cleaning a company name that no regex can reach, the borderline calls on what reads as a chain

### The two-phase handshake


A script cannot just "call an LLM" and trust the answer, so the command stops in the middle and hands over.

1. **Phase 1.** The script resolves listings, pulls the worst reviews, and writes <output>.pending.json, then stops
1. **Phase 2.** Claude reads the pending file and writes the summaries as JSON, keyed by lead
1. **Phase 3.** The script validates the format, the length, that nothing was invented, and that no health detail identifying a condition was carried across, then merges them in

Review summaries are the one place a model can produce something fluent and wrong, and it goes straight into a cold email. Putting a schema check between generation and output means that failure is caught rather than shipped.


### The command

- **/dental-reviews <input.csv>** takes domain, city, state and zip. Nothing else. Keys come from the environment, never the files
- It reuses the ZIP pool from cache and fetches only the ZIPs it does not already hold
- Every row gets a status and a partial result. A row is never silently dropped
- **Proof it travels:** examples/ holds a 17-domain Arizona list, a state this project never sourced. 17 of 17 resolved, and it re-runs from cache at $0

### Claude Code as the build environment


Writing, running and correcting the scripts in one loop against real data is how the mistakes surfaced at all. The cache is what made that affordable: after fixing a rule, re-running the stage over 1,353 practices costs nothing, so I could afford to be wrong repeatedly.


## 6. Qualification: why the rules are ordered

- Rules run in a fixed order and the **first failure wins**, so every rejected row has one reason and the counts add up instead of double-counting
- **The footer scan earns its place.** A name blocklist never catches "South Tampa Dentistry" because DSOs keep the local brand, but its own site says "a brand owned by Heartland Dental LLC". It needed a guard: a practice’s own copyright line is not a parent company

## 7. What the corrections actually taught me

1. **A secondary attribute should not condemn a record.** Real practices run in-house labs, so only the primary Google category disqualifies
1. **A domain is not a key.** One practice ran in three cities and shared one location’s reviews until the key became domain plus ZIP
1. **Check the cache before the budget guard.** Mine ran the other way, so an exhausted budget hid free cached answers and the empty result overwrote a good file

## 8. Where it landed, and what is missing

- 1,422 Maps listings + 8,295 WebMD dentists → **1,353 practices** → **729 qualified** → **8 under 4.0**
- **That 8 is the market, not the pipeline.** Reaching 50 needs roughly $21.60 against a $5 free ceiling per account
- So I shipped those 8 with a labelled 4.0–4.5 tier and added a segment the brief did not ask for: **healthy ratings that are slipping right now**. At $0.007 a probe against $1.86 per under-4.0 lead, 286 probed found **26 actively declining**. **68 leads carry evidence**, for $14.29 across three accounts
- **Missing, and stated:** LinkedIn empty on all 42, because I never ran the lookup. WebMD address lookup unfinished at 987 practices, ~$0.30 and ~1 hour. 12 leads have ratings and competitors but no review bullets
