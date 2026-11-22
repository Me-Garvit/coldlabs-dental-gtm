"""
Stage 2b - resolve WebMD practices on Google Maps, by location.

This inverts how WebMD is used. Elsewhere it only corroborates places the Maps
sweep already found, which caps a free source holding 2,178 practices at
whatever the paid sweep could afford (827). But we never needed a match: we
need the practice's Google reviews, and we already know where it is. So we ask
Maps directly, and WebMD becomes a lead source whose rows arrive with dentist
names already attached.

    python scripts/webmd_resolve.py --limit 258 --per-query 3 --max-charge-usd 4.00

HOW THE QUERY IS BUILT - and why the obvious ways fail
------------------------------------------------------
  1. "{street}, {city}, {state} {zip}"  -> an address/building GEOCODE.
     categoryName None or "Building", no rating, no reviews. Useless, and it
     slips past a naive address check because an address matches itself.
  2. "dentist {street}, {city}, ..."    -> a real but WRONG listing. A Silber
     Rd query returned "Heritage Dental - Katy", a different suburb, because
     Google weighted "dentist" over the street. Its address fields came back
     null, so nothing could verify it.
  3. "Dr. {name} {city}" only helps where the practice is named after the
     dentist. Measured against the 419 practices already matched: 18%.

So we geo-constrain. Each address is geocoded free, then handed to Maps as a
search URL anchored to those coordinates:

    https://www.google.com/maps/search/dentist/@{lat},{lng},19z

startUrls is an array, so addresses still batch into one actor run, and the
search area is a city block rather than a metro.

ACCEPTANCE - a result is taken only if it is a real business listing (a
category beyond "Building", and a rating or review count) AND either:
  * its normalised street + ZIP equals the WebMD address        -> "address"
  * or it is the ONLY dental listing within --max-metres        -> "proximity"
Anything else is rejected rather than guessed; `resolved_by` records which
rule fired so proximity rows stay auditable.

WHAT IS PROVEN, AND WHAT IS NOT
--------------------------------
Proven:
  * Geocoding is solved. US Census landed 14/14 within 120m (worst 90m) on
    practices whose true Maps coordinates we already knew; Nominatim managed
    9/14 and missed by up to 5.7km. Units must be stripped first - "1114
    Silber Rd Ste F" fails where "1114 Silber Rd" succeeds (10/10 vs 2/6).
    Both geocoders are free and keyless; Census is primary, Nominatim backup.
  * The geo-anchored URL returns real business listings WITH populated address
    fields - the thing the text queries could not do. A live run returned
    "NW Dental" (4.6, 152 reviews, full address) for a Silber Rd anchor.
  * The distance guard works: that NW Dental result sat 345m away and was
    correctly rejected rather than accepted as the practice at that address.
  * The business-listing guard works: address geocodes and "Building" results
    are filtered out.

NOT proven - the Apify account hit its $5.00 ceiling first:
  * The end-to-end accept rate. Not one candidate has been successfully
    resolved yet, because every live attempt was spent diagnosing the query
    rather than running the finished version.
  * Whether --per-query 1-2 surfaces the right practice. The one geo-anchored
    run returned a single listing 345m away, which suggests either that
    1114 Silber Rd has no dental listing on Google (a stale WebMD row - a real
    and expected failure mode) or that more results per anchor are needed.
    Start at --per-query 3.

Expected cost and yield for the 258 in-grid candidates, at --per-query 3:
258 x 3 x $0.005 = about $3.90. Accept rate is unknown; geocoding is no longer
the limiting factor, so the remaining risk is Google surfacing the right place
and WebMD rows being stale. Treat 258 as an upper bound, not a forecast, and
run --limit 20 first to measure the real rate before committing the rest.
"""
import argparse
import csv
import hashlib
import json
import math
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import maps_sweep
import normalize as nz
import providers

ROOT = pathlib.Path(__file__).resolve().parent.parent
UNMATCHED = ROOT / "data" / "webmd_unmatched.json"
RAW = ROOT / "data" / "raw_maps.csv"
GEOCACHE = ROOT / "data" / "cache" / "geocode.json"
REPORT = ROOT / "data" / "webmd_resolve_report.json"

# US Census first, Nominatim as fallback. Measured against 14 practices whose
# true Maps coordinates we already know: Census landed 14/14 within 120m
# (worst 90m); Nominatim managed 9/14 and missed by as much as 5.7km, usually
# by falling back to a road centroid. Both are free and need no key.
CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "coldlabs-dental-research/1.0 (batch geocoder; contact via project maintainer)"

DENTAL = ("dentist", "dental", "orthodont", "endodont", "periodont",
          "prosthodont", "oral surg", "denture")


def load_geocache():
    if GEOCACHE.exists():
        return json.loads(GEOCACHE.read_text())
    return {}


def _census(q):
    r = requests.get(CENSUS, params={"address": q, "benchmark": "Public_AR_Current",
                                     "format": "json"}, timeout=25)
    r.raise_for_status()
    m = r.json()["result"]["addressMatches"]
    if not m:
        return None
    c = m[0]["coordinates"]
    return {"lat": c["y"], "lng": c["x"], "q": q, "via": "census"}


def _nominatim(q):
    r = requests.get(NOMINATIM, params={"q": q, "format": "json", "limit": 1,
                                        "countrycodes": "us"},
                     headers={"User-Agent": UA}, timeout=25)
    r.raise_for_status()
    d = r.json()
    time.sleep(1.1)   # Nominatim asks for <=1 req/sec
    if not d:
        return None
    return {"lat": float(d[0]["lat"]), "lng": float(d[0]["lon"]), "q": q,
            "via": "nominatim"}


def geocode(street, city, state, zipcode, cache, log=print):
    """
    Free, cached, unit-stripped geocoding.

    The unit must go first: "1114 Silber Rd Ste F" fails, "1114 Silber Rd"
    succeeds - 10/10 versus 2/6 in testing.
    """
    base, _ = nz.split_unit(street)
    q = f"{base}, {city}, {state} {zipcode}"
    key = "v2:" + hashlib.sha256(q.encode()).hexdigest()[:20]
    if key in cache:
        return cache[key]
    hit = None
    for fn in (_census, _nominatim):
        try:
            hit = fn(q)
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            log(f"    {fn.__name__} failed for {q}: {exc}")
            hit = None
        if hit:
            break
    cache[key] = hit
    GEOCACHE.parent.mkdir(parents=True, exist_ok=True)
    GEOCACHE.write_text(json.dumps(cache))
    return hit


def metres(a_lat, a_lng, b_lat, b_lng):
    R = 6371000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(h))


def is_business(it):
    cats = {(it.get("categoryName") or "").lower()}
    cats |= {c.lower() for c in (it.get("categories") or [])}
    cats.discard("")
    if not cats or cats <= {"building", "corporate office", "office"}:
        return False
    return it.get("totalScore") is not None or it.get("reviewsCount") is not None


def is_dental(it):
    hay = ((it.get("categoryName") or "") + " " +
           " ".join(it.get("categories") or []) + " " + (it.get("title") or "")).lower()
    return any(t in hay for t in DENTAL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--offset", type=int, default=0,
                    help="skip the first N candidates (for spot-testing)")
    ap.add_argument("--max-charge-usd", type=float, default=0.05)
    ap.add_argument("--per-query", type=int, default=2)
    ap.add_argument("--zoom", type=int, default=19)
    ap.add_argument("--max-metres", type=float, default=120.0)
    ap.add_argument("--min-doctors", type=int, default=1)
    ap.add_argument("--geocode-only", action="store_true")
    args = ap.parse_args()

    grid = {r["zip"] for r in csv.DictReader((ROOT / "scripts/config/zips.csv").open())}
    cand = [c for c in json.loads(UNMATCHED.read_text())
            if c["zip"] in grid and c["street"] and len(c["doctors"]) >= args.min_doctors]
    cand.sort(key=lambda c: -len(c["doctors"]))
    pick = cand[args.offset:args.offset + args.limit]
    print(f"{len(cand)} in-grid candidates; taking {len(pick)} "
          f"(offset {args.offset})")

    cache = load_geocache()
    located = []
    for c in pick:
        g = geocode(c["street"], c["city"], c["state"], c["zip"], cache)
        if g:
            located.append((c, g))
    print(f"geocoded {len(located)}/{len(pick)} (free)")
    if args.geocode_only:
        return

    urls = [{"url": f"https://www.google.com/maps/search/dentist/@{g['lat']},{g['lng']},{args.zoom}z"}
            for _, g in located]
    print(f"estimated: {len(urls) * args.per_query} places "
          f"= ${len(urls) * args.per_query * 0.005:.3f}")

    payload = {
        "startUrls": urls,
        "maxCrawledPlacesPerSearch": args.per_query,
        "countryCode": "us", "language": "en",
        "skipClosedPlaces": False, "scrapePlaceDetailPage": False,
        "maxReviews": 0, "maxImages": 0, "maxQuestions": 0,
        "scrapeContacts": False, "includeWebResults": False,
    }
    try:
        items, meta = providers.call("maps_search", payload,
                                     max_charge_usd=args.max_charge_usd,
                                     label=f"webmd geo-resolve {len(urls)}")
    except (providers.BudgetExceeded, providers.ProviderError) as exc:
        print(f"STOP: {exc}")
        return
    print(f"  {len(items)} places via {meta['provider']} ${meta['usd']:.3f}"
          f"{' (cached)' if meta.get('cached') else ''}\n")

    accepted, rejected = [], []
    for (c, g) in located:
        near = []
        for it in items:
            loc = it.get("location") or {}
            if loc.get("lat") is None:
                continue
            d = metres(g["lat"], g["lng"], loc["lat"], loc["lng"])
            if d <= args.max_metres and is_business(it) and is_dental(it):
                near.append((d, it))
        near.sort(key=lambda x: x[0])

        want = nz.full_addr_key(c["street"], c["zip"])
        hit = how = None
        for d, it in near:
            if want and nz.full_addr_key(it.get("street") or "",
                                         it.get("postalCode") or "") == want:
                hit, how = it, "address"
                break
        if not hit and len(near) == 1:
            hit, how = near[0][1], "proximity"

        if hit:
            row = maps_sweep.flatten(hit, c["zip"], "", meta["provider"] + "+webmd")
            row["scraped_at"] = row["scraped_at"] or ""
            accepted.append({"row": row, "doctors": c["doctors"], "how": how,
                             "metres": round(near[0][0], 1)})
            print(f"  OK[{how:<9}] {row['name'][:30]:<32} {row['rating'] or '-':>4}"
                  f" ({row['review_count'] or 0:>4})  {len(c['doctors'])} dentist(s)")
        else:
            rejected.append({"address": f"{c['street']}, {c['city']} {c['zip']}",
                             "doctors": len(c["doctors"]),
                             "dental_within_range": len(near),
                             "titles": [t.get("title") for _, t in near][:4]})
            print(f"  skip {c['street'][:34]:<36} {len(near)} dental listing(s) in range")

    if accepted:
        prior = list(csv.DictReader(RAW.open()))
        have = {r["place_id"] for r in prior}
        new = [a["row"] for a in accepted if a["row"]["place_id"] not in have]
        with RAW.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=maps_sweep.FIELDS)
            w.writeheader()
            w.writerows(prior + new)
        print(f"\nappended {len(new)} new places to {RAW}")

    rep = {"candidates": len(cand), "queried": len(located),
           "accepted": len(accepted), "rejected": len(rejected),
           "accept_rate": round(len(accepted) / max(len(located), 1), 3),
           "by_rule": {h: sum(1 for a in accepted if a["how"] == h)
                       for h in ("address", "proximity")},
           "spend_usd": meta["usd"],
           "accepted_detail": [{"name": a["row"]["name"], "rating": a["row"]["rating"],
                                "reviews": a["row"]["review_count"], "how": a["how"],
                                "metres": a["metres"], "doctors": a["doctors"][:5]}
                               for a in accepted],
           "rejected_detail": rejected[:20]}
    REPORT.write_text(json.dumps(rep, indent=2))
    print(json.dumps({k: v for k, v in rep.items() if not k.endswith("_detail")}, indent=1))
    print(json.dumps(providers.budget_report(), indent=1))


if __name__ == "__main__":
    main()
