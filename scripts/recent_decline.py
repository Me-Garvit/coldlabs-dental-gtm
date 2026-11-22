"""
The "good rating, bad momentum" segment.

A 4.8-rated practice with four 1-star reviews this quarter is a different and
often better prospect than a 3.4-rated one: they have a reputation worth
defending, and the trend is against them.

Finding it needs review DATES, not the rating. The cheap way to get them is to
sort reviews `newest` and pass `reviewsStartDate`, so we only pay for reviews
inside the window instead of pulling a practice's whole history. For a
high-volume practice that is the difference between ~$0.05 and ~$1.00.

Output: data/recent_decline.csv
"""
import argparse
import csv
import json
import pathlib
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import providers
import reviews as rv

ROOT = pathlib.Path(__file__).resolve().parent.parent


def i(v):
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=int, default=90)
    ap.add_argument("--min-rating", type=float, default=4.5)
    ap.add_argument("--min-bad", type=int, default=20,
                    help="total 1-2 star reviews; high-volume practices")
    ap.add_argument("--limit", type=int, default=4)
    ap.add_argument("--max-charge-usd", type=float, default=0.18)
    ap.add_argument("--max-reviews", type=int, default=120,
                    help="per practice, inside the window")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip domains already probed, and append to the file")
    args = ap.parse_args()

    since = (date.today() - timedelta(days=args.window_days)).isoformat()
    rows = list(csv.DictReader(open(ROOT / "data/enriched.csv")))
    cand = []
    for r in rows:
        bad = i(r["reviews_1star"]) + i(r["reviews_2star"])
        if f(r["rating"]) >= args.min_rating and bad >= args.min_bad:
            cand.append((r, bad))
    # most bad reviews per total review = fastest-slipping, cheapest window
    cand.sort(key=lambda p: -(p[1] / max(i(p[0]["review_count"]), 1)))
    prior, done = [], set()
    outp = ROOT / "data/recent_decline.csv"
    if args.skip_existing and outp.exists():
        prior = list(csv.DictReader(outp.open()))
        done = {r["domain"] for r in prior}
        cand = [c for c in cand if c[0]["domain"] not in done]
        print(f"skipping {len(done)} already probed")
    pick = cand[:args.limit]

    print(f"{len(cand)} candidates rated >={args.min_rating} with >={args.min_bad} "
          f"bad reviews; probing {len(pick)} for activity since {since}")

    pids = [r["place_id"] for r, _ in pick]
    payload = {"placeIds": pids, "maxReviews": args.max_reviews,
               "reviewsSort": "newest", "reviewsStartDate": since,
               "language": "en", "reviewsOrigin": "all", "personalData": True}
    try:
        items, meta = providers.call("reviews", payload,
                                     max_charge_usd=args.max_charge_usd,
                                     label=f"recent-decline {len(pids)} places "
                                           f"since {since}")
    except (providers.BudgetExceeded, providers.ProviderError) as exc:
        print(f"STOP: {exc}")
        return
    print(f"  {len(items)} reviews in window via {meta['provider']} "
          f"${meta['usd']:.3f}{' (cached)' if meta.get('cached') else ''}")

    by_place = {}
    for it in items:
        by_place.setdefault(it.get("placeId") or "", []).append(it)

    out = []
    for r, bad in pick:
        got = by_place.get(r["place_id"], [])
        recent_bad = [x for x in got if i(x.get("stars") or x.get("rating")) in (1, 2)]
        with_text = [x for x in recent_bad if rv.review_text(x)]
        replied = sum(1 for x in recent_bad if rv.owner_replied(x))
        dates = sorted((rv.review_date(x) for x in recent_bad if rv.review_date(x)),
                       reverse=True)
        out.append({
            "company_name": r["company_name"], "domain": r["domain"],
            "city": r["city"], "state": r["state"], "zip": r["zip"],
            "rating": r["rating"], "review_count": r["review_count"],
            "bad_reviews_lifetime": bad,
            "reviews_in_window": len(got),
            "bad_in_window": len(recent_bad),
            "bad_in_window_with_text": len(with_text),
            "owner_replied_in_window": f"{replied} of {len(recent_bad)}",
            "last_bad_review_date": dates[0] if dates else "",
            "window_days": args.window_days,
        })

    all_rows = prior + out
    with outp.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    out = all_rows
    print()
    print(f"{'practice':<34} {'rate':>5} {'revs':>5} {'win':>4} {'bad':>4} {'text':>5}  replied   last")
    for o in sorted(out, key=lambda o: -i(o["bad_in_window"])):
        print(f"{o['company_name'][:32]:<34} {o['rating']:>5} {o['review_count']:>5} "
              f"{o['reviews_in_window']:>4} {o['bad_in_window']:>4} "
              f"{o['bad_in_window_with_text']:>5}  {o['owner_replied_in_window']:<8} "
              f"{o['last_bad_review_date']}")
    print(f"\nwrote {outp}")
    print(json.dumps(providers.budget_report(), indent=1))


if __name__ == "__main__":
    main()
