"""
Stage 1a - Google Maps sourcing on a ZIP grid.

One "dentist {ZIP}" query per ZIP, not one per city: a Maps search tops out
around 120 results, so a city-level query silently drops most of the market.

ZIPs are batched into one actor run per metro because the primary actor
(compass/crawler-google-places) refuses runs with a cost ceiling under $0.50.
Each batch is cached whole, so re-runs cost nothing.

Output: data/raw_maps.csv - every row kept. This file is also the competitor
pool used in Stage 6.
"""
import argparse
import csv
import json
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import providers

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw_maps.csv"

FIELDS = ["place_id", "name", "address", "street", "city", "state", "zip",
          "phone", "phone_raw", "website", "rating", "review_count",
          "reviews_1star", "reviews_2star", "reviews_3star", "reviews_4star",
          "reviews_5star", "permanently_closed", "temporarily_closed",
          "primary_category", "categories", "lat", "lng", "maps_url",
          "search_zip", "search_metro", "provider", "scraped_at"]

STATE_ABBR = {"Texas": "TX", "Florida": "FL", "Arizona": "AZ", "Georgia": "GA",
              "California": "CA", "Nevada": "NV", "Louisiana": "LA"}


def load_zips(path, only_metros=None, limit=None):
    rows = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            if only_metros and r["metro"] not in only_metros:
                continue
            rows.append(r)
    rows.sort(key=lambda r: (int(r["priority"]), r["zip"]))
    return rows[:limit] if limit else rows


def flatten(it, search_zip, metro, provider):
    dist = it.get("reviewsDistribution") or {}
    st = it.get("state") or ""
    return {
        "place_id": it.get("placeId") or "",
        "name": (it.get("title") or "").strip(),
        "address": it.get("address") or "",
        "street": it.get("street") or "",
        "city": it.get("city") or "",
        "state": STATE_ABBR.get(st, st)[:2].upper() if st else "",
        "zip": (it.get("postalCode") or "")[:5],
        "phone": it.get("phoneUnformatted") or "",
        "phone_raw": it.get("phone") or "",
        "website": it.get("website") or "",
        "rating": it.get("totalScore") if it.get("totalScore") is not None else "",
        "review_count": it.get("reviewsCount") if it.get("reviewsCount") is not None else "",
        "reviews_1star": dist.get("oneStar", ""),
        "reviews_2star": dist.get("twoStar", ""),
        "reviews_3star": dist.get("threeStar", ""),
        "reviews_4star": dist.get("fourStar", ""),
        "reviews_5star": dist.get("fiveStar", ""),
        "permanently_closed": bool(it.get("permanentlyClosed")),
        "temporarily_closed": bool(it.get("temporarilyClosed")),
        "primary_category": it.get("categoryName") or "",
        "categories": "|".join(it.get("categories") or []),
        "lat": (it.get("location") or {}).get("lat", ""),
        "lng": (it.get("location") or {}).get("lng", ""),
        "maps_url": it.get("url") or "",
        "search_zip": search_zip,
        "search_metro": metro,
        "provider": provider,
        "scraped_at": it.get("scrapedAt") or "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zips", default=str(ROOT / "scripts/config/zips.csv"))
    ap.add_argument("--per-zip", type=int, default=30,
                    help="max places per ZIP query")
    ap.add_argument("--metros", default="", help="comma list, blank = all")
    ap.add_argument("--limit-zips", type=int, default=None)
    ap.add_argument("--batch-cap-usd", type=float, default=1.00,
                    help="cost ceiling per metro batch")
    ap.add_argument("--terms", default="dentist",
                    help="comma-separated search terms; each is run per ZIP. "
                         "Plain 'dentist' is relevance-ranked and buries "
                         "low-rated practices, so terms like 'medicaid dentist' "
                         "surface a different slice of the market.")
    ap.add_argument("--append", action="store_true",
                    help="merge into the existing raw_maps.csv instead of replacing it")
    args = ap.parse_args()

    metros = [m.strip() for m in args.metros.split(",") if m.strip()] or None
    zips = load_zips(args.zips, metros, args.limit_zips)
    by_metro = defaultdict(list)
    for z in zips:
        by_metro[z["metro"]].append(z)

    print(f"budget: {json.dumps(providers.budget_report())}")
    print(f"sweeping {len(zips)} ZIPs across {len(by_metro)} metros "
          f"at <= {args.per_zip} places/ZIP")

    all_rows, stats = [], []
    for metro, zrows in by_metro.items():
        search_terms = [t.strip() for t in args.terms.split(",") if t.strip()]
        terms, zip_of_term = [], {}
        for z in zrows:
            for t in search_terms:
                q = f"{t} {z['zip']}"
                terms.append(q)
                zip_of_term[q] = z
        payload = {
            "searchStringsArray": terms,
            "countryCode": "us",
            "language": "en",
            "maxCrawledPlacesPerSearch": args.per_zip,
            "skipClosedPlaces": False,
            "scrapePlaceDetailPage": False,
            "maxReviews": 0, "maxImages": 0, "maxQuestions": 0,
            "scrapeContacts": False, "includeWebResults": False,
            "scrapeDirectories": False,
        }
        try:
            items, meta = providers.call("maps_search", payload,
                                         max_charge_usd=args.batch_cap_usd,
                                         label=f"sweep {metro} ({len(terms)} zips)")
        except providers.BudgetExceeded as e:
            print(f"  STOP: {e}")
            break
        cached = " (cached)" if meta.get("cached") else ""
        print(f"  {metro}: {len(items)} places via {meta['provider']} "
              f"${meta['usd']:.3f}{cached}")
        stats.append({"metro": metro, "zips": len(terms), "places": len(items),
                      "provider": meta["provider"], "usd": meta["usd"]})
        for it in items:
            term = it.get("searchString") or ""
            z = zip_of_term.get(term, {})
            all_rows.append(flatten(it, z.get("zip", ""), metro, meta["provider"]))

    # de-dup identical place IDs returned by neighbouring ZIP queries
    seen, rows = set(), []
    for r in all_rows:
        k = r["place_id"] or (r["phone"], r["name"])
        if k in seen:
            continue
        seen.add(k)
        rows.append(r)

    if args.append and RAW.exists():
        with RAW.open() as fh:
            prior = list(csv.DictReader(fh))
        have = {r["place_id"] for r in prior}
        added = [r for r in rows if r["place_id"] not in have]
        print(f"  append: {len(added)} new places on top of {len(prior)} existing")
        rows = prior + added

    RAW.parent.mkdir(parents=True, exist_ok=True)
    with RAW.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"\nwrote {len(rows)} unique places -> {RAW} "
          f"({len(all_rows)-len(seen)} duplicate hits across queries)")
    print(f"budget now: {json.dumps(providers.budget_report())}")
    (ROOT / "data" / "sweep_stats.json").write_text(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
