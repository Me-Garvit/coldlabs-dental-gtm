"""
Stage 2b - resolve WebMD practices on Google Maps BY ADDRESS.

This is the step the build plan called "targeted Maps lookup" and it inverts
how WebMD is used. Elsewhere WebMD only corroborates places the Maps sweep
already found, which caps it at whatever the sweep could afford. But we do not
actually need a match: we need the practice's Google reviews, and we already
know its address. So we ask Maps directly.

That turns WebMD from a corroborator into a lead source. Every row it resolves
arrives with the dentist names already attached, which is the decision-maker
column, and then flows through the normal downstream pipeline because the
resolved place is appended to raw_maps.csv in the sweep's own schema.

Guardrail: a result is accepted only when its normalised street + ZIP matches
the WebMD address it came from AND it looks like a real business listing
(a category, and a rating or review count). Shared medical buildings and
near-miss geocodes are rejected rather than guessed, because attaching the
wrong business would put a real dentist's name on someone else's bad reviews.

STATUS: NOT YET WORKING. The idea is sound and the plumbing is complete, but
the query is not. Two attempts, both out of budget to iterate on:

  1. "{street}, {city}, {state} {zip}"
     -> returns an address/building GEOCODE: categoryName None or "Building",
        no rating, no reviews, no website. Useless, and it slipped through a
        naive address-only check, which is why the business-listing test above
        now exists.

  2. "dentist {street}, {city}, {state} {zip}"
     -> returns a real dentist listing, but the WRONG one: querying
        "dentist 1114 Silber Rd Ste F, Houston, TX 77055" returned
        "Heritage Dental - Katy" (4.8, 2336 reviews), a different suburb.
        Worse, street/city/postalCode came back null on that record, so the
        address guard could not verify it and rejected it - correct behaviour,
        but it means the guard cannot confirm anything either.

Next things to try, cheapest first:
  * Query the dentist instead of the address: "Dr. {doctor_name} {city} {state}".
    WebMD gives us the name and solo practices are usually listed under it.
  * Set scrapePlaceDetailPage=True (+$0.002/place) so street/postalCode are
    reliably populated and the address guard can actually do its job.
  * Feed a Maps place-search URL via startUrls with lat/lng from the address,
    which constrains the search area instead of trusting Google's text match.

Budget to develop this properly: roughly $1-2. Budget remaining when it was
written: $0.006.
"""
import argparse
import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import maps_sweep
import normalize as nz
import providers

ROOT = pathlib.Path(__file__).resolve().parent.parent
UNMATCHED = ROOT / "data" / "webmd_unmatched.json"
RAW = ROOT / "data" / "raw_maps.csv"
REPORT = ROOT / "data" / "webmd_resolve_report.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--max-charge-usd", type=float, default=0.04)
    ap.add_argument("--per-query", type=int, default=1,
                    help="places per address query")
    ap.add_argument("--grid-only", action="store_true", default=True)
    ap.add_argument("--min-doctors", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    grid = {r["zip"] for r in csv.DictReader((ROOT / "scripts/config/zips.csv").open())}
    cand = json.loads(UNMATCHED.read_text())
    if args.grid_only:
        cand = [c for c in cand if c["zip"] in grid]
    cand = [c for c in cand if len(c["doctors"]) >= args.min_doctors and c["street"]]
    # a practice with more dentists is more likely a real office, not a stale row
    cand.sort(key=lambda c: -len(c["doctors"]))
    pick = cand[:args.limit]

    # A bare address returns an address/building GEOCODE - no category, no
    # rating, no reviews - which is useless to us and, worse, passes a naive
    # address check. The query has to name the business we want.
    queries = [f"dentist {c['street']}, {c['city']}, {c['state']} {c['zip']}"
               for c in pick]
    print(f"{len(cand)} resolvable WebMD practices; querying {len(pick)}")
    print(f"estimated: {len(pick) * args.per_query} places "
          f"= ${len(pick) * args.per_query * 0.005:.3f}")
    for c, q in list(zip(pick, queries))[:10]:
        print(f"   {q:<52} {len(c['doctors'])} dentist(s)")
    if args.dry_run:
        return

    payload = {
        "searchStringsArray": queries,
        "countryCode": "us", "language": "en",
        "maxCrawledPlacesPerSearch": args.per_query,
        "skipClosedPlaces": False, "scrapePlaceDetailPage": False,
        "maxReviews": 0, "maxImages": 0, "maxQuestions": 0,
        "scrapeContacts": False, "includeWebResults": False,
    }
    try:
        items, meta = providers.call("maps_search", payload,
                                     max_charge_usd=args.max_charge_usd,
                                     label=f"webmd resolve {len(pick)} addresses")
    except (providers.BudgetExceeded, providers.ProviderError) as exc:
        print(f"STOP: {exc}")
        return
    print(f"\n  {len(items)} places via {meta['provider']} ${meta['usd']:.3f}"
          f"{' (cached)' if meta.get('cached') else ''}\n")

    by_query = {}
    for it in items:
        by_query.setdefault(it.get("searchString") or "", []).append(it)

    accepted, rejected = [], []
    for c, q in zip(pick, queries):
        want = nz.full_addr_key(c["street"], c["zip"])
        hit = None
        for it in by_query.get(q, []):
            got = nz.full_addr_key(it.get("street") or "", it.get("postalCode") or "")
            if not (want and got == want):
                continue
            # It must be an actual business listing, not a geocoded address or
            # an office building: a real listing has a category and a rating.
            cats = {(it.get("categoryName") or "").lower()}
            cats |= {c2.lower() for c2 in (it.get("categories") or [])}
            cats.discard("")
            if not cats or cats <= {"building", "corporate office"}:
                continue
            if it.get("totalScore") is None and it.get("reviewsCount") is None:
                continue
            hit = it
            break
        if hit:
            row = maps_sweep.flatten(hit, c["zip"], "", meta["provider"] + "+webmd")
            accepted.append((c, row))
            print(f"  OK   {row['name'][:34]:<36} {row['rating'] or '-':>4}"
                  f" ({row['review_count'] or 0:>4}) {row['zip']}"
                  f"  <- {len(c['doctors'])} dentist(s)")
        else:
            got = [nz.full_addr_key(x.get('street') or '', x.get('postalCode') or '')
                   for x in by_query.get(q, [])]
            rejected.append({"address": q, "doctors": c["doctors"],
                             "wanted": want, "got": got,
                             "titles": [x.get("title") for x in by_query.get(q, [])]})
            print(f"  skip {q[:46]:<48} wanted {want!r} got {got}")

    if accepted:
        with RAW.open() as fh:
            prior = list(csv.DictReader(fh))
        have = {r["place_id"] for r in prior}
        new = [row for _, row in accepted if row["place_id"] not in have]
        with RAW.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=maps_sweep.FIELDS)
            w.writeheader()
            w.writerows(prior + new)
        print(f"\nappended {len(new)} new places to {RAW} "
              f"({len(accepted) - len(new)} already present)")

    rep = {"candidates": len(cand), "queried": len(pick),
           "accepted": len(accepted), "rejected": len(rejected),
           "accept_rate": round(len(accepted) / max(len(pick), 1), 3),
           "spend_usd": meta["usd"], "rejected_detail": rejected[:20]}
    REPORT.write_text(json.dumps(rep, indent=2))
    print(json.dumps({k: v for k, v in rep.items() if k != "rejected_detail"}, indent=1))
    print(json.dumps(providers.budget_report(), indent=1))


if __name__ == "__main__":
    main()
