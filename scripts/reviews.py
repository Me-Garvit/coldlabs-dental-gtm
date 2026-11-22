"""
Stage 6a - find each lead's Google listing and pull its worst reviews.

Review budget trick: the Maps listing already tells us how many 1- and 2-star
reviews exist (reviewsDistribution). Sorted by lowestRanking, the bad ones come
first, so a place with six 1-2 star reviews needs ~9 fetched, not 25. Places
are bucketed by the depth they actually need and each bucket is one actor run.
On a $0.0006/review meter that is the difference between a run we can afford
and one we cannot.
"""
import json
import pathlib
import re
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import normalize as nz
import providers

ROOT = pathlib.Path(__file__).resolve().parent.parent

DEPTH_BUCKETS = [8, 15, 25]
MIN_BAD_WANTED = 5


def needed_depth(one_star, two_star, cap=25):
    try:
        bad = int(float(one_star or 0)) + int(float(two_star or 0))
    except (TypeError, ValueError):
        bad = 0
    if bad <= 0:
        return 0
    # a cushion for bad reviews with no text, which we cannot use
    want = min(cap, bad + 4)
    for b in DEPTH_BUCKETS:
        if want <= b:
            return b
    return cap


def fetch_reviews(leads, max_charge_usd, cap=25, log=print):
    """
    leads: iterable of dicts with place_id, reviews_1star, reviews_2star.
    Returns {place_id: [review dicts]}.
    """
    buckets = defaultdict(list)
    skipped = []
    for ld in leads:
        d = needed_depth(ld.get("reviews_1star"), ld.get("reviews_2star"), cap)
        if d == 0:
            skipped.append(ld["place_id"])
            continue
        buckets[d].append(ld["place_id"])

    if skipped:
        log(f"    {len(skipped)} leads have no 1-2 star reviews at all; "
            f"skipping (no spend)")

    out = {}
    total = sum(len(v) * k for k, v in buckets.items())
    log(f"    review plan: {sum(len(v) for v in buckets.values())} places, "
        f"<= {total} reviews, <= ${total*0.0006:.2f}")

    for depth, pids in sorted(buckets.items()):
        est = len(pids) * depth * 0.0006
        payload = {
            "placeIds": pids,
            "maxReviews": depth,
            "reviewsSort": "lowestRanking",
            "language": "en",
            "reviewsOrigin": "all",
            "personalData": True,
        }
        try:
            items, meta = providers.call(
                "reviews", payload,
                max_charge_usd=min(max_charge_usd, est * 1.5 + 0.05),
                label=f"reviews depth={depth} n={len(pids)}")
        except (providers.BudgetExceeded, providers.ProviderError) as exc:
            log(f"    review fetch failed at depth {depth}: {exc}")
            continue
        log(f"      depth {depth}: {len(pids)} places -> {len(items)} reviews "
            f"via {meta['provider']} ${meta['usd']:.3f}"
            f"{' (cached)' if meta.get('cached') else ''}")
        for it in items:
            pid = it.get("placeId") or it.get("placeid") or ""
            if pid:
                out.setdefault(pid, []).append(it)
    return out


REVIEW_TEXT_KEYS = ("text", "textTranslated", "reviewText")


def review_text(r):
    for k in REVIEW_TEXT_KEYS:
        v = r.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return ""


def review_date(r):
    for k in ("publishedAtDate", "publishAtDate", "publishedAt"):
        v = r.get(k)
        if v:
            return str(v)[:10]
    return ""


def reviewer_name(r):
    for k in ("name", "reviewerName", "author"):
        v = r.get(k)
        if v:
            return str(v).strip()
    return ""


def owner_replied(r):
    for k in ("responseFromOwnerText", "ownerResponse", "responseFromOwner"):
        v = r.get(k)
        if v and str(v).strip():
            return True
    return False


def pick_bad_reviews(reviews, want=MIN_BAD_WANTED):
    """1-2 star, with text, newest first, top `want`."""
    bad = []
    for r in reviews or []:
        try:
            stars = int(float(r.get("stars") or r.get("rating") or 0))
        except (TypeError, ValueError):
            continue
        if stars not in (1, 2):
            continue
        txt = review_text(r)
        if not txt:
            continue
        bad.append({
            "stars": stars,
            "text": re.sub(r"\s+", " ", txt)[:1200],
            "date": review_date(r),
            "reviewer": reviewer_name(r),
            "owner_replied": owner_replied(r),
            "review_id": r.get("reviewId") or r.get("reviewUrl") or "",
        })
    # dedupe on (reviewer, date, first 60 chars)
    seen, uniq = set(), []
    for b in bad:
        k = (b["reviewer"], b["date"], b["text"][:60])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(b)
    uniq.sort(key=lambda b: b["date"], reverse=True)
    return uniq[:want], uniq


def resolve_by_domain(domain, zipcode, pool_by_zip, log=print):
    """Find the listing in the ZIP pool whose website root domain matches."""
    cands = [p for p in pool_by_zip.get(zipcode, [])
             if nz.root_domain(p.get("website", "")) == domain]
    if not cands:
        return None, "no_domain_match_in_zip"
    if len(cands) == 1:
        return cands[0], "domain_match"
    exact = [c for c in cands if nz.norm_zip(c.get("zip")) == zipcode]
    return (exact or cands)[0], "domain_match_multiple"
