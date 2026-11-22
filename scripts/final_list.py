"""
Stage 5 + Stage 7 - the rating filter, and assembling the deliverable.

  prep : enriched.csv -> under-4.0 rows -> data/reviews_input.csv
         (exactly the four columns /dental-reviews takes: domain, city, state,
         zip - so the command is run on this list the same way a stranger would
         run it on theirs)
  join : fold the command's output back onto the lead rows -> final_list.csv
"""
import argparse
import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import normalize as nz

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENRICHED = ROOT / "data" / "enriched.csv"
RV_INPUT = ROOT / "data" / "reviews_input.csv"
RV_OUTPUT = ROOT / "data" / "reviews_output.csv"
FINAL = ROOT / "data" / "final_list.csv"

# doctors sits third on purpose: it is the decision-maker column and the whole
# reason WebMD is in the pipeline. Buried at column 23 nobody finds it.
FINAL_COLS = [
    "company_name", "doctors", "doctor_count", "domain", "rating_tier",
    "google_rating", "review_count", "city", "state", "zip",
    "bad_reviews", "bad_review_count_90d", "last_bad_review_date",
    "owner_replied_to_bad",
    "competitor_1_name", "competitor_1_rating", "competitor_1_reviews",
    "competitor_2_name", "competitor_2_rating", "competitor_2_reviews",
    "competitor_3_name", "competitor_3_rating", "competitor_3_reviews",
    "rating_gap", "specialty", "source", "webmd_profiles", "linkedin_url",
    "phone", "address", "maps_url", "place_id", "name_raw", "builder_domain",
    "competitors", "status", "scraped_at",
]


def rows_of(p):
    with open(p) as fh:
        return list(csv.DictReader(fh))


def prep(max_rating):
    rows = rows_of(ENRICHED)
    keep = []
    for r in rows:
        try:
            rating = float(r["rating"])
        except (ValueError, KeyError, TypeError):
            continue
        if 0 < rating < max_rating:
            keep.append(r)
    keep.sort(key=lambda r: float(r["rating"]))
    with RV_INPUT.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["domain", "city", "state", "zip"])
        w.writeheader()
        for r in keep:
            w.writerow({"domain": r["domain"], "city": r["city"],
                        "state": r["state"], "zip": nz.norm_zip(r["zip"])})
    print(f"Stage 5: {len(keep)} of {len(rows)} qualified practices rate "
          f"under {max_rating}")
    print(f"wrote {RV_INPUT}")
    return keep


def join():
    leads = {}
    for r in rows_of(ENRICHED):
        leads.setdefault((r["domain"], nz.norm_zip(r["zip"])), r)
    out = []
    for e in rows_of(RV_OUTPUT):
        key = (e["domain"], nz.norm_zip(e["zip"]))
        base = leads.get(key, {})
        row = {c: "" for c in FINAL_COLS}
        for c in FINAL_COLS:
            if c in e and e[c] != "":
                row[c] = e[c]
            elif c in base:
                row[c] = base[c]
        row["google_rating"] = e.get("google_rating") or base.get("rating", "")
        row["review_count"] = e.get("review_count") or base.get("review_count", "")
        row["company_name"] = base.get("company_name") or e.get("company_name", "")
        try:
            rt = float(row["google_rating"])
            row["rating_tier"] = "under_4.0" if rt < 4.0 else "4.0_to_4.5"
        except (TypeError, ValueError):
            row["rating_tier"] = "unknown"
        out.append(row)
    out.sort(key=lambda r: (float(r["google_rating"] or 9),
                            -int(float(r["review_count"] or 0))))
    with FINAL.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FINAL_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)
    stats = {
        "final_rows": len(out),
        "with_bad_reviews": sum(1 for r in out if r["bad_reviews"]),
        "with_3_competitors": sum(1 for r in out if r["competitor_3_name"]),
        "with_any_competitor": sum(1 for r in out if r["competitor_1_name"]),
        "with_doctor_names": sum(1 for r in out if r["doctors"]),
        "with_linkedin": sum(1 for r in out if r["linkedin_url"]),
        "source_both": sum(1 for r in out if r["source"] == "Both"),
        "tier_under_4.0": sum(1 for r in out if r["rating_tier"] == "under_4.0"),
        "tier_4.0_to_4.5": sum(1 for r in out if r["rating_tier"] == "4.0_to_4.5"),
    }
    (ROOT / "data" / "final_stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))
    print(f"wrote {FINAL}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["prep", "join"])
    ap.add_argument("--max-rating", type=float, default=4.0)
    a = ap.parse_args()
    prep(a.max_rating) if a.stage == "prep" else join()
