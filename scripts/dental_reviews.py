"""
Stage 6 - the repeatable step.

    python scripts/dental_reviews.py --in leads.csv --out enriched.csv

Input: a CSV with domain, city, state, zip. Nothing else is required.
Output: the same rows with Google rating, recent bad reviews and the top three
better-rated practices in the same ZIP.

Runs on any fresh list with no edits. Keys come from the environment
(APIFY_TOKEN and/or OUTSCRAPER_API_KEY) and either alone works.

Review summarisation is a two-phase handshake, because the summaries are
Claude's judgement call, not a regex:
  phase 1 (this script)  -> writes <out>.pending.json with the raw bad reviews
  phase 2 (Claude)       -> writes the summaries back as JSON
  phase 3 (this script, --summaries) -> validates them and writes the CSV
Running without --summaries still produces a complete CSV; the bad-review
column just carries the raw quotes instead of the one-line summaries.
"""
import argparse
import csv
import json
import pathlib
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import competitors as comp
import normalize as nz
import providers
import reviews as rv

ROOT = pathlib.Path(__file__).resolve().parent.parent
POOL_CSV = ROOT / "data" / "raw_maps.csv"

OUT_COLS = [
    "domain", "city", "state", "zip", "company_name", "google_rating",
    "review_count", "bad_reviews", "bad_review_count_90d",
    "last_bad_review_date", "owner_replied_to_bad", "competitors",
    "competitor_1_name", "competitor_1_rating", "competitor_1_reviews",
    "competitor_2_name", "competitor_2_rating", "competitor_2_reviews",
    "competitor_3_name", "competitor_3_rating", "competitor_3_reviews",
    "rating_gap", "maps_url", "place_id", "status",
]


def load_pool():
    pool = defaultdict(list)
    if POOL_CSV.exists():
        with POOL_CSV.open() as fh:
            for r in csv.DictReader(fh):
                z = nz.norm_zip(r.get("zip"))
                if z:
                    pool[z].append(r)
    return pool


def ensure_zips(pool, zips, max_charge_usd, per_zip, log=print):
    """Fetch any ZIP we do not already hold. Cached, so re-runs are free."""
    missing = sorted(z for z in zips if not pool.get(z))
    if not missing:
        log(f"    all {len(zips)} ZIPs already in the local pool (no spend)")
        return
    log(f"    {len(missing)} ZIP(s) not in the pool: {', '.join(missing)}")
    payload = {
        "searchStringsArray": [f"dentist {z}" for z in missing],
        "countryCode": "us", "language": "en",
        "maxCrawledPlacesPerSearch": per_zip,
        "skipClosedPlaces": False, "scrapePlaceDetailPage": False,
        "maxReviews": 0, "maxImages": 0, "maxQuestions": 0,
        "scrapeContacts": False, "includeWebResults": False,
    }
    try:
        items, meta = providers.call("maps_search", payload,
                                     max_charge_usd=max_charge_usd,
                                     label=f"zip pool {len(missing)} zips")
    except (providers.BudgetExceeded, providers.ProviderError) as exc:
        log(f"    ZIP pool fetch failed: {exc}")
        return
    log(f"      {len(items)} places via {meta['provider']} ${meta['usd']:.3f}")
    import maps_sweep
    for it in items:
        row = maps_sweep.flatten(it, "", "", meta["provider"])
        z = nz.norm_zip(row["zip"])
        if z:
            pool[z].append(row)


def normalise_input(path):
    with open(path) as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit("input CSV is empty")
    have = {c.lower().strip() for c in rows[0]}
    required = {"domain", "city", "state", "zip"}
    missing = required - have
    if missing:
        raise SystemExit(f"input CSV missing required column(s): {sorted(missing)}")
    out, seen = [], set()
    for r in rows:
        r = {(k or "").lower().strip(): (v or "").strip() for k, v in r.items()}
        d = nz.root_domain(r["domain"])
        z = nz.norm_zip(r["zip"])
        if not d:
            print(f"  skipping unusable domain {r['domain']!r}")
            continue
        if (d, z) in seen:
            continue
        seen.add((d, z))
        out.append({**r, "domain": d, "zip": z})
    return out


def within_90d(d):
    try:
        return (date.today() - datetime.strptime(d, "%Y-%m-%d").date()) <= timedelta(days=90)
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", required=True)
    ap.add_argument("--summaries", default=None,
                    help="JSON of Claude review summaries (phase 3)")
    ap.add_argument("--max-charge-usd", type=float, default=1.20)
    ap.add_argument("--per-zip", type=int, default=25)
    ap.add_argument("--review-cap", type=int, default=25)
    args = ap.parse_args()

    leads = normalise_input(args.inp)
    print(f"input: {len(leads)} unique rows")
    print(f"budget before run: {json.dumps(providers.budget_report())}")

    pool = load_pool()
    ensure_zips(pool, {l["zip"] for l in leads if l["zip"]},
                args.max_charge_usd * 0.6, args.per_zip)

    # --- resolve each domain to a Google listing -------------------------
    resolved, statuses = {}, {}
    for l in leads:
        hit, how = rv.resolve_by_domain(l["domain"], l["zip"], pool)
        if hit:
            resolved[l["domain"]] = hit
            statuses[l["domain"]] = []
        else:
            statuses[l["domain"]] = ["listing_not_found"]
    print(f"    resolved {len(resolved)}/{len(leads)} domains to a Google listing")

    # --- reviews ----------------------------------------------------------
    pend = [{"place_id": h["place_id"],
             "reviews_1star": h.get("reviews_1star"),
             "reviews_2star": h.get("reviews_2star")}
            for h in resolved.values() if h.get("place_id")]
    raw_reviews = rv.fetch_reviews(pend, args.max_charge_usd * 0.4,
                                   cap=args.review_cap) if pend else {}

    prior = comp.pool_mean([p for z in pool.values() for p in z])
    summaries = json.loads(pathlib.Path(args.summaries).read_text()) \
        if args.summaries else {}

    out_rows, queue = [], []
    for l in leads:
        dom = l["domain"]
        st = list(statuses[dom])
        hit = resolved.get(dom)
        row = {c: "" for c in OUT_COLS}
        row.update({"domain": dom, "city": l["city"], "state": l["state"],
                    "zip": l["zip"]})
        if not hit:
            row["status"] = "listing_not_found"
            out_rows.append(row)
            continue

        from clean_enrich import clean_name
        row["company_name"] = clean_name(hit.get("name", ""))[0]
        row["google_rating"] = hit.get("rating", "")
        row["review_count"] = hit.get("review_count", "")
        row["maps_url"] = hit.get("maps_url", "")
        row["place_id"] = hit.get("place_id", "")

        top, all_bad = rv.pick_bad_reviews(raw_reviews.get(hit["place_id"], []))
        if not top:
            st.append("no_bad_reviews")
        elif len(top) < 3:
            st.append(f"only_{len(top)}_bad_reviews")
        row["bad_review_count_90d"] = sum(1 for b in all_bad if within_90d(b["date"]))
        row["last_bad_review_date"] = all_bad[0]["date"] if all_bad else ""
        if top:
            row["owner_replied_to_bad"] = \
                f"{sum(1 for b in top if b['owner_replied'])} of {len(top)}"

        key = f"{dom}|{hit['place_id']}"
        if top:
            if key in summaries:
                row["bad_reviews"] = "\n".join(f"- {s}" for s in summaries[key])
            else:
                row["bad_reviews"] = "\n".join(
                    f"- {b['reviewer']} ({b['stars']}*, {b['date']}): "
                    f"{b['text'][:140]}" for b in top)
                queue.append({"key": key, "domain": dom,
                              "company_name": row["company_name"],
                              "reviews": top})

        cands, cstat = comp.find_competitors(
            {**hit, "specialty": hit.get("specialty", "")}, pool, prior)
        if cstat:
            st.append(cstat)
        row["competitors"] = "\n".join(f"- {c['name']}" for c in cands)
        for i, c in enumerate(cands[:3], 1):
            row[f"competitor_{i}_name"] = c["name"]
            row[f"competitor_{i}_rating"] = c["rating"]
            row[f"competitor_{i}_reviews"] = c["reviews"]
        if cands and row["google_rating"] not in ("", None):
            gap = cands[0]["rating"] - float(row["google_rating"])
            row["rating_gap"] = f"+{gap:.1f}"

        row["status"] = ";".join(st) if st else "ok"
        out_rows.append(row)

    outp = pathlib.Path(args.out)
    if not resolved and outp.exists() and outp.stat().st_size > 0:
        raise SystemExit(
            f"refusing to overwrite {outp}: this run resolved 0 of {len(leads)} "
            f"rows (provider or budget failure), and the existing file has "
            f"data. Fix the cause and re-run; nothing was changed.")
    with outp.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_COLS)
        w.writeheader()
        w.writerows(out_rows)

    if queue:
        pf = outp.with_suffix(".pending.json")
        pf.write_text(json.dumps(queue, indent=2))
        print(f"\n{len(queue)} leads need review summaries -> {pf}")
        print("  phase 2: Claude summarises, then re-run with --summaries <file>")

    from collections import Counter
    counts = Counter(s for r in out_rows for s in r["status"].split(";"))
    summary = {
        "rows": len(out_rows),
        "resolved": len(resolved),
        "status_counts": dict(counts.most_common()),
        "with_bad_reviews": sum(1 for r in out_rows if r["bad_reviews"]),
        "with_3_competitors": sum(1 for r in out_rows if r["competitor_3_name"]),
        "budget": providers.budget_report(),
    }
    outp.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"wrote {outp}")


if __name__ == "__main__":
    main()
