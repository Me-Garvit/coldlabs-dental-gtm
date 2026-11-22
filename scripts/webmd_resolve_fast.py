"""
Stage 2b (fast) - resolve WebMD practices on Google Maps, cheaply.

Same job as webmd_resolve.py, different engine. That one drives
compass/crawler-google-places through geo-anchored search URLs at roughly
$0.014 per address. This one uses solutionssmart/fast-google-map-scraper,
which costs $0.0001 per place returned - about 50x less - and hands back
rating, review_count and website in the same call, so no second lookup is
needed to qualify a practice.

It also batches: many addresses go into one run via `searchTerms`, so 1,478
addresses are ~30 runs rather than 1,478, and minutes rather than hours.

The catch, and why the old resolver is kept: results come back as a flat list
with no link to the query that produced them. A search for a Ross Ave address
returned a supermarket at that address and a Brident branch three miles away.
So matching is done here, on normalised street + ZIP, exactly as before -
nothing is accepted on proximity or ranking alone.

Known difference: this actor returns place_id in hex CID form
("0x8640c423...:0xf6b46d37...") rather than Google's "ChIJ..." form, so these
rows cannot be fed straight into the compass reviews scraper by placeId. The
CID converts to a Maps URL (?cid=<decimal>) when reviews are needed.
"""
import argparse
import concurrent.futures as cf
import csv
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import maps_sweep
import normalize as nz
import providers

ROOT = pathlib.Path(__file__).resolve().parent.parent
UNMATCHED = ROOT / "data" / "webmd_unmatched.json"
RAW = ROOT / "data" / "raw_maps.csv"
REPORT = ROOT / "data" / "webmd_resolve_fast_report.json"

ADDR = re.compile(r"^(.*?),\s*([^,]+),\s*([A-Z]{2})\s+(\d{5})")


def to_row(it, provider):
    """Map this actor's shape onto the sweep's schema."""
    addr = it.get("address") or ""
    m = ADDR.match(addr)
    street, city, state, zc = (m.group(1), m.group(2), m.group(3), m.group(4)) if m else ("", "", "", "")
    coords = it.get("coordinates") or {}
    cats = it.get("categories") or []
    return {
        "place_id": it.get("place_id") or "",
        "name": (it.get("name") or "").strip(),
        "address": addr, "street": street, "city": city, "state": state,
        "zip": zc,
        "phone": nz.norm_phone(it.get("phone")), "phone_raw": it.get("phone") or "",
        "website": it.get("website") or "",
        "rating": it.get("rating") if it.get("rating") is not None else "",
        "review_count": it.get("review_count") if it.get("review_count") is not None else "",
        # this actor does not return a star distribution; reviews.py falls back
        # to a default depth when these are blank
        "reviews_1star": "", "reviews_2star": "", "reviews_3star": "",
        "reviews_4star": "", "reviews_5star": "",
        "permanently_closed": bool(it.get("permanently_closed")),
        "temporarily_closed": False,
        "primary_category": cats[0] if cats else "",
        "categories": "|".join(cats),
        "lat": coords.get("lat", ""), "lng": coords.get("lng", ""),
        "maps_url": "", "search_zip": zc, "search_metro": "",
        "provider": provider + "+webmd", "scraped_at": "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--batch", type=int, default=50, help="addresses per run")
    ap.add_argument("--max-places", type=int, default=3, help="places per address")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--max-charge-usd", type=float, default=0.60)
    ap.add_argument("--grid-only", action="store_true")
    args = ap.parse_args()

    grid = {r["zip"] for r in csv.DictReader((ROOT / "scripts/config/zips.csv").open())}
    cand = [c for c in json.loads(UNMATCHED.read_text()) if c["street"] and c["zip"]]
    if args.grid_only:
        cand = [c for c in cand if c["zip"] in grid]
    cand.sort(key=lambda c: -len(c["doctors"]))
    cand = cand[:args.limit]
    print(f"{len(cand)} WebMD practices to resolve")

    terms, by_key = [], {}
    for c in cand:
        base, _ = nz.split_unit(c["street"])
        terms.append(f"dentist {base}, {c['city']}, {c['state']} {c['zip']}")
        k = nz.full_addr_key(c["street"], c["zip"])
        if k:
            by_key.setdefault(k, c)

    batches = [terms[i:i + args.batch] for i in range(0, len(terms), args.batch)]
    est = len(terms) * args.max_places * 0.0001
    print(f"{len(batches)} runs of <= {args.batch} addresses, {args.concurrency} at a time")
    print(f"estimated: {len(terms) * args.max_places} places = ${est:.2f}")

    def run(ix):
        i, chunk = ix
        payload = {"mode": "search", "searchTerms": chunk,
                   "maxPlaces": args.max_places, "outputMode": "placesOnly",
                   "skipClosedPlaces": False}
        return providers.call("place_lookup", payload,
                              max_charge_usd=args.max_charge_usd / len(batches) * 2.5,
                              timeout_s=max(900, len(chunk) * 25),
                              label=f"webmd fast-resolve b{i+1} n={len(chunk)}")

    items, done, stopped = [], 0, False
    with cf.ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(run, (i, b)): i for i, b in enumerate(batches)}
        for f in cf.as_completed(futs):
            done += 1
            try:
                it, mt = f.result()
            except providers.BudgetExceeded as exc:
                if not stopped:
                    print(f"  BUDGET STOP at {done}/{len(batches)}: {exc}")
                    stopped = True
                continue
            except providers.ProviderError as exc:
                print(f"  run {futs[f]+1} failed: {str(exc)[:110]}")
                continue
            items.extend(it)
            if done % 5 == 0 or done == len(batches):
                print(f"  {done}/{len(batches)} runs, {len(items)} places, "
                      f"${providers.account_usage_usd():.3f} spent")

    print(f"\n{len(items)} places returned; matching on street + ZIP")
    accepted, seen = [], set()
    for it in items:
        row = to_row(it, "apify:solutionssmart-maps")
        k = nz.full_addr_key(row["street"], row["zip"])
        if not k or k not in by_key or k in seen:
            continue
        if row["rating"] == "" and row["review_count"] == "":
            continue
        seen.add(k)
        accepted.append({"row": row, "doctors": by_key[k]["doctors"]})

    print(f"matched {len(accepted)} of {len(cand)} addresses "
          f"({len(accepted)/max(len(cand),1)*100:.0f}%)")

    if accepted:
        prior = list(csv.DictReader(RAW.open()))
        have = {r["place_id"] for r in prior}
        new = [a["row"] for a in accepted if a["row"]["place_id"] not in have]
        with RAW.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=maps_sweep.FIELDS)
            w.writeheader()
            w.writerows(prior + new)
        print(f"appended {len(new)} new places -> {RAW}")

    REPORT.write_text(json.dumps({
        "candidates": len(cand), "places_returned": len(items),
        "accepted": len(accepted),
        "accept_rate": round(len(accepted) / max(len(cand), 1), 3),
        "sample": [{"name": a["row"]["name"], "rating": a["row"]["rating"],
                    "reviews": a["row"]["review_count"],
                    "website": a["row"]["website"][:60],
                    "doctors": a["doctors"][:3]} for a in accepted[:25]],
    }, indent=2))
    print(json.dumps(providers.budget_report(), indent=1))


if __name__ == "__main__":
    main()
