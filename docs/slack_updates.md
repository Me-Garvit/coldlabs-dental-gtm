# Slack updates to the account manager

Three messages: kickoff, the blocker as it actually happened, and the handoff.
Bracketed figures are filled from `data/funnel.json` and `data/final_stats.json`.

---

## 1. Kickoff — day 1

> Starting the dental review-management list today.
>
> Plan: Google Maps via Apify as the primary source, with a second Apify actor
> as the fallback, plus WebMD as the independent second source across Houston,
> Dallas, Tampa and Orlando. Queries run per ZIP rather than per city — a Maps
> search caps out around 120 results, so "dentist Houston" would silently miss
> most of the market.
>
> Filters: cutting DSOs and chains, closed listings, no-website, and practices
> with more than 3 locations, then keeping only ratings under 4.0. Every lead
> will carry its recent 1–2 star reviews and the top 3 better-rated practices in
> the same ZIP.
>
> First sample tomorrow EOD for a gut check on copy fit.

---

## 2. Blocker — mid-task

> Two things worth flagging, one solved and one that needs a decision.
>
> **Solved:** Healthgrades, which I had down as the backup directory, returns 403
> to automated requests. I didn't try to work around it — that's the site saying
> no. WebMD carried the second source on its own and did it well: 8,295 dentists
> pulled, 1,542 doctor names attached to 379 practices, which gives us
> decision-maker names for free.
>
> **Needs a decision:** the under-4.0 pool is far thinner than we assumed.
> Measured on 761 real practices: **74% of dentists sit at 4.8–5.0 and only 2.8%
> are below 4.0.** After the DSO and chain filters, the qualified yield is
> **under 1 lead per 100 places scraped**.
>
> At Apify's metered rate that means ~5,400 places, about **$21.60**, to reach 50
> qualified leads. The account is on the free $5/month plan, so this run lands at
> roughly [N] leads rather than 50.
>
> I tested whether smarter queries fix it — "medicaid dentist", "emergency dental
> clinic", "affordable dentist" instead of plain "dentist {ZIP}". They don't:
> 144 queries returned 47 new places and one marginal lead. The only real lever
> is volume.
>
> Three options: (a) ~$25 of Apify credit and I deliver the 50+ as briefed,
> (b) move the threshold to under 4.5, where the yield is 3.89 per 100 and 50 is
> reachable, or (c) ship [N] under-4.0 leads and say so plainly. Everything else
> — the command, QA, the rejected tab, decision makers — is built and tested
> either way. Your call.

---

## 3. Final handoff

> List is done: [Sheet link].
>
> **[N] practices** pass every filter, from [RAW] raw listings ([REJ] rejected —
> top reasons: no website [n], DSO/chain [n], off-grid geography [n]). Each row
> carries its recent 1–2 star reviews as one-line summaries, the owner-reply rate
> on those reviews, and the top 3 better-rated practices in the same ZIP with
> ratings and review counts.
>
> The `/dental-reviews` command is in the repo and runs on any
> `domain, city, state, zip` list with no edits — `examples/` has an input and
> output from a state this project never sourced, as proof.
>
> **Gaps, stated honestly:**
> - The list is [N], not 50. The under-4.0 pool genuinely isn't there at this
>   budget — detail in the write-up, and the fix is credit, not code.
> - LinkedIn found for [n]% — most practices this size have no company page. The
>   blanks are real, not padding.
> - [k] rows have fewer than 3 same-ZIP competitors. We never fill from a
>   neighbouring ZIP, because "the clinic down the road" is the whole pitch.
>
> Cost came to **$[C]** total, **$[CPL] per qualified lead**. Decision-maker plan
> is in `docs/decision_makers.md`; I can run a 20-practice email pilot next and
> report the real hit rate per step.
