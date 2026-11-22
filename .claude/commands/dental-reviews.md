---
description: Enrich a dental lead list with recent bad Google reviews and same-ZIP better-rated competitors
argument-hint: <input.csv> [output.csv]
allowed-tools: Bash, Read, Write, Edit
---

# /dental-reviews

Takes a CSV of dental practices and adds, for each one, its Google rating, its
most recent 1-2 star reviews written as one-line summaries, and the top three
better-rated practices in the same ZIP.

**Input:** `$1` - a CSV with columns `domain, city, state, zip`. Nothing else is
required; extra columns are ignored.
**Output:** `$2` (default: `<input>_enriched.csv`) - the same rows with the
review and competitor columns added.

Credentials come from the environment: `APIFY_TOKEN` and/or
`OUTSCRAPER_API_KEY`. Either one alone is enough.

---

## Run it

### Phase 1 - fetch

```bash
cd "$CLAUDE_PROJECT_DIR"
./.venv/bin/python scripts/dental_reviews.py --in "$1" --out "${2:-${1%.csv}_enriched.csv}"
```

This resolves every domain to a Google listing, pulls its worst reviews and
finds its same-ZIP competitors. It prints a run summary (rows, status counts,
spend, budget headroom) and, if any lead has bad reviews needing summarising,
writes `<output>.pending.json`.

If there is no `.pending.json`, the CSV is already final. Stop here.

### Phase 2 - summarise the reviews (this is your job, not the script's)

Read `<output>.pending.json`. It is a list of leads, each with up to five bad
reviews (`reviewer`, `stars`, `date`, `text`).

Turn **each review** into one line:

```
First name + last initial - 4-5 word summary of the complaint
```

Examples:

```
Sarah M. - waited two hours past appointment
James R. - billed for work never done
Priya K. - crown fell out twice
```

Rules:

- Use the reviewer's first name and the initial of their last name. One name
  only (e.g. "Dave") becomes `Dave - ...`.
- The summary is **4-5 words**, lowercase, no final period. It states the
  complaint, not your opinion of it.
- Summarise **non-English reviews in English**.
- Never invent a detail that is not in the review text.
- Never carry over a health detail that identifies a condition (diagnoses,
  pregnancy, named illnesses). Describe the service failure instead.

Write the result as JSON to `<output>.summaries.json`, keyed by the `key` field
from the pending file, in the same order as the reviews:

```json
{
  "example-dental.com|ChIJabc123": [
    "Sarah M. - waited two hours past appointment",
    "James R. - billed for work never done"
  ]
}
```

### Phase 3 - write the final CSV

```bash
cd "$CLAUDE_PROJECT_DIR"
./.venv/bin/python scripts/dental_reviews.py --in "$1" \
  --out "${2:-${1%.csv}_enriched.csv}" \
  --summaries "${2:-${1%.csv}_enriched.csv}.summaries.json"
./.venv/bin/python scripts/validate_output.py "${2:-${1%.csv}_enriched.csv}"
```

Phase 1 is fully cached, so re-running it costs nothing. The validator checks
bullet format, competitor rules and name hygiene, and writes
`data/qa_report.md`. Report its FAIL count to the user; fix and re-run rather
than shipping a row that fails.

---

## Output columns

| column | example |
|---|---|
| `google_rating`, `review_count` | `3.4`, `87` |
| `bad_reviews` | `- Sarah M. - waited two hours past appointment` |
| `bad_review_count_90d`, `last_bad_review_date` | `3`, `2026-11-02` |
| `owner_replied_to_bad` | `1 of 5` |
| `competitors` | `- Lakeside Family Dental` |
| `competitor_1..3_name / _rating / _reviews` | `Lakeside Family Dental / 4.8 / 412` |
| `rating_gap` | `+1.4` |
| `status` | `ok`, or the codes below |

## Status codes

| situation | behaviour | status |
|---|---|---|
| fewer than 3 bad reviews | return what exists, never padded | `only_N_bad_reviews` |
| no 1-2 star reviews with text | review columns blank | `no_bad_reviews` |
| listing not found after both providers | row kept, review/competitor columns blank | `listing_not_found` |
| fewer than 3 qualifying competitors | return what exists, strict same-ZIP | `only_N_competitors` / `no_competitors_in_zip` |
| provider error or rate limit | backoff, retry, fall back, then row-level error | `provider_error` |
| re-run after interruption | resumes from cache | n/a |

## Spend

Every provider call is capped and logged to `data/spend_ledger.jsonl`. The run
refuses to start a new call once `RUN_BUDGET_USD` is reached, so an unattended
re-run cannot drain the account. Check headroom any time with:

```bash
./.venv/bin/python scripts/providers.py
```
