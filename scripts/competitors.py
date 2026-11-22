"""
Stage 6b - the better-rated practice in the same ZIP.

Strict same-ZIP: we never fill from a neighbouring ZIP, because the whole pitch
is "the clinic down the road is taking your patients". A lead with two
qualifying competitors ships with two and says so.

Ranking is a Bayesian average, not the raw star rating, so 4.8 on 400 reviews
outranks 5.0 on 30 - which is what a patient comparing the two would feel.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import normalize as nz
from clean_enrich import clean_name

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "scripts/config/categories.json").read_text())
BLOCK = [l.strip().lower() for l in
         (ROOT / "scripts/config/dso_blocklist.txt").read_text().splitlines()
         if l.strip() and not l.startswith("#")]

MIN_RATING = 4.5
MIN_REVIEWS = 25
PRIOR_WEIGHT = 50.0


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def is_chain(name, website):
    hay = f"{(name or '').lower()} {nz.root_domain(website)}"
    return any(tok in hay for tok in BLOCK)


def cats_of(p):
    c = [p.get("primary_category", "")] + (p.get("categories", "") or "").split("|")
    return {x.strip().lower() for x in c if x.strip()}


def bayesian(rating, count, prior_mean):
    v = max(0, count)
    return (v / (v + PRIOR_WEIGHT)) * rating + (PRIOR_WEIGHT / (v + PRIOR_WEIGHT)) * prior_mean


def pool_mean(pool):
    vals = [_f(p.get("rating")) for p in pool if _f(p.get("rating")) > 0]
    return sum(vals) / len(vals) if vals else 4.5


def find_competitors(lead, pool_by_zip, prior_mean, top_n=3):
    """Return (list_of_competitors, status_code)."""
    z = nz.norm_zip(lead.get("zip"))
    lead_rating = _f(lead.get("rating"))
    lead_pid = lead.get("place_id")
    lead_specialties = set((lead.get("specialty") or "").split("|")) - {"general", ""}

    allowed = set(CFG["competitor_categories"]) | lead_specialties
    cands = []
    for p in pool_by_zip.get(z, []):
        if p.get("place_id") == lead_pid:
            continue
        if nz.norm_zip(p.get("zip")) != z:
            continue
        if str(p.get("permanently_closed", "")).lower() == "true" or \
           str(p.get("temporarily_closed", "")).lower() == "true":
            continue
        cats = cats_of(p)
        if not (cats & allowed):
            continue
        # a specialist is only a competitor to a lead with the same specialty
        spec = cats & set(CFG["specialist_categories"])
        if spec and not (spec & lead_specialties):
            continue
        if is_chain(p.get("name"), p.get("website")):
            continue
        r, n = _f(p.get("rating")), _i(p.get("review_count"))
        if r < MIN_RATING or n < MIN_REVIEWS or r <= lead_rating:
            continue
        cands.append({
            "name": clean_name(p.get("name", ""))[0],
            "name_raw": p.get("name", ""),
            "rating": r, "reviews": n,
            "zip": nz.norm_zip(p.get("zip")),
            "place_id": p.get("place_id", ""),
            "score": round(bayesian(r, n, prior_mean), 4),
        })

    cands.sort(key=lambda c: (-c["score"], -c["reviews"]))
    # one listing per practice name
    seen, out = set(), []
    for c in cands:
        k = nz.name_key(c["name"])
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
        if len(out) == top_n:
            break

    if not out:
        return [], "no_competitors_in_zip"
    if len(out) < top_n:
        return out, f"only_{len(out)}_competitors"
    return out, ""
