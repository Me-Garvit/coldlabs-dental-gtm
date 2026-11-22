# Repeatability proof — a state we never sourced

`/dental-reviews` was built for the TX/FL list, then run unchanged on **Arizona**
(Phoenix, ZIPs 85004 and 85018), which this project never sourced.

| file | what it is |
|---|---|
| `fresh_list_input.csv` | 17 Phoenix dental domains — `domain, city, state, zip`, nothing else |
| `fresh_list_output.csv` | the command's real output |
| `fresh_list_output.summaries.json` | the review summaries Claude wrote in phase 2 |
| `fresh_list_output.pending.json` | the raw bad reviews handed to Claude in phase 1 |

## Result

```
rows                17
resolved            17 / 17
with bad reviews    10
with 3 competitors  10

status counts:  ok 5 · no_bad_reviews 7 · no_competitors_in_zip 7 · only_1_bad_reviews 1
```

Reproduce it:

```bash
./.venv/bin/python scripts/dental_reviews.py \
  --in examples/fresh_list_input.csv \
  --out examples/fresh_list_output.csv \
  --per-zip 15 --summaries examples/fresh_list_output.summaries.json
```

Everything is cached, so this **re-runs at $0** — which we verified by running it
again after the Apify budget was fully exhausted. It returned the same 17 rows.

## Reading the QA report on this file

Running the validator over the example reports 2 FAIL and 30 WARN. All three are
correct behaviour, not defects:

- **`no_chains` (1)** — `westerndental.com` is a DSO. The command deliberately
  does **not** filter chains; that is Stage 3's job on the main pipeline. Its
  presence here shows the QA check catching a chain that was never filtered.
- **`min_reviews` (1)** — `1800dentist.com` has 2 reviews. Again, the command
  enriches whatever list you hand it; the ≥10 review rule lives in qualification.
- **`competitor_zip` (30)** — the validator checks competitors against
  `data/raw_maps.csv`, which holds only the TX/FL pool. Arizona lives in the
  command's own cache, so the lookup cannot match. Expected for a fresh state.

This is the honest point of the example: the command works on an unseen list, and
the QA layer correctly flags rows that would not survive the full pipeline.
