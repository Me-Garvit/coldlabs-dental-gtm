"""
Stage 2 - normalize, roll WebMD dentists up into practices, match them against
Google Maps, dedupe across sources.

WebMD listing cards carry the dentist, not the practice name, and only ~16% of
them expose a phone. So the rollup key is the normalized street + ZIP (suite
stripped), with phone as a confirming signal. A WebMD address that matches more
than one Maps listing (a shared medical building) is only attached when the
phone agrees too - otherwise the doctors are left unattached and the case is
logged, because guessing would put the wrong dentist's name on a lead.

Output: data/merged.csv (one row per practice) + data/merge_report.json
"""
import argparse
import csv
import json
import pathlib
import sys
from collections import defaultdict

from rapidfuzz import fuzz

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import normalize as nz

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAPS = ROOT / "data" / "raw_maps.csv"
WEBMD = ROOT / "data" / "raw_webmd.csv"
OUT = ROOT / "data" / "merged.csv"
REJ = ROOT / "data" / "rejected_stage2.csv"
REPORT = ROOT / "data" / "merge_report.json"

OUT_FIELDS = [
    "lead_id", "name", "address", "street", "city", "state", "zip", "phone",
    "website", "rating", "review_count", "reviews_1star", "reviews_2star",
    "permanently_closed", "temporarily_closed", "primary_category", "categories",
    "place_id", "maps_url", "lat", "lng", "search_zip", "search_metro",
    "source", "maps_verified", "webmd_profiles", "doctors", "doctor_count",
    "provider", "scraped_at",
]


def read_csv(p):
    if not p.exists():
        return []
    with p.open() as fh:
        return list(csv.DictReader(fh))


def build_maps(rows):
    """Dedupe Maps rows; return (practices, dupes)."""
    by_place, dupes = {}, []
    for r in rows:
        pid = r["place_id"]
        if not pid:
            continue
        if pid in by_place:
            dupes.append({**r, "reject_code": "duplicate",
                          "duplicate_of": by_place[pid]["place_id"],
                          "note": "same place_id"})
            continue
        by_place[pid] = r

    # second pass: same phone + ZIP, or same normalized street + ZIP with a
    # close name -> one office listed twice
    keep, seen_phone, seen_addr = {}, {}, {}
    for pid, r in by_place.items():
        ph, z = nz.norm_phone(r["phone"]), nz.norm_zip(r["zip"])
        ak = nz.full_addr_key(r["street"], r["zip"])
        pk = f"{ph}|{z}" if ph and z else ""
        dup_of = None
        if pk and pk in seen_phone:
            dup_of, why = seen_phone[pk], "same phone + ZIP"
        elif ak and ak in seen_addr:
            other = keep[seen_addr[ak]]
            if fuzz.token_set_ratio(nz.name_seq(r["name"]),
                                    nz.name_seq(other["name"])) >= 85:
                dup_of, why = seen_addr[ak], "same street + ZIP, name match"
        if dup_of:
            dupes.append({**r, "reject_code": "duplicate",
                          "duplicate_of": dup_of, "note": why})
            continue
        keep[pid] = r
        if pk:
            seen_phone.setdefault(pk, pid)
        if ak:
            seen_addr.setdefault(ak, pid)
    return keep, dupes


def rollup_webmd(rows):
    """Group dentists into practices by normalized street + ZIP (phone assists)."""
    groups = defaultdict(list)
    unusable = []
    for r in rows:
        ak = nz.full_addr_key(r["street"], r["zip"])
        ph = nz.norm_phone(r["phone"])
        key = ak or (f"phone:{ph}" if ph else "")
        if not key:
            unusable.append(r)
            continue
        groups[key].append(r)
    out = []
    for key, members in groups.items():
        phones = {nz.norm_phone(m["phone"]) for m in members} - {""}
        out.append({
            "addr_key": key if not key.startswith("phone:") else "",
            "phones": phones,
            "street": members[0]["street"], "city": members[0]["city"],
            "state": members[0]["state"], "zip": nz.norm_zip(members[0]["zip"]),
            "doctors": sorted({m["doctor_name"] for m in members if m["doctor_name"]}),
            "profiles": [m["profile_url"] for m in members],
            "specialties": sorted({m["specialty"] for m in members if m["specialty"]}),
        })
    return out, unusable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--restrict-webmd-to-target-zips", action="store_true",
                    default=True)
    args = ap.parse_args()

    maps_rows = read_csv(MAPS)
    webmd_rows = read_csv(WEBMD)
    print(f"raw: {len(maps_rows)} Maps rows, {len(webmd_rows)} WebMD dentists")

    maps_keep, maps_dupes = build_maps(maps_rows)
    print(f"Maps after dedupe: {len(maps_keep)} ({len(maps_dupes)} dupes)")

    target_zips = {r["zip"] for r in maps_rows}
    wm_practices, wm_unusable = rollup_webmd(webmd_rows)
    print(f"WebMD rolled up: {len(wm_practices)} practices from "
          f"{len(webmd_rows)} dentists ({len(wm_unusable)} unusable rows)")

    # index Maps for matching
    idx_phone, idx_addr = defaultdict(list), defaultdict(list)
    for pid, r in maps_keep.items():
        ph = nz.norm_phone(r["phone"])
        if ph:
            idx_phone[ph].append(pid)
        ak = nz.full_addr_key(r["street"], r["zip"])
        if ak:
            idx_addr[ak].append(pid)

    attached, ambiguous, unmatched = {}, [], []
    for wp in wm_practices:
        hit = None
        for ph in wp["phones"]:
            if idx_phone.get(ph):
                hit = idx_phone[ph][0]
                break
        if not hit and wp["addr_key"]:
            cands = idx_addr.get(wp["addr_key"], [])
            if len(cands) == 1:
                hit = cands[0]
            elif len(cands) > 1:
                confirmed = [c for c in cands
                             if nz.norm_phone(maps_keep[c]["phone"]) in wp["phones"]]
                if len(confirmed) != 1:
                    # A solo practice is often listed under the dentist's own
                    # name ("Dr. Sharel Sly"). When exactly one candidate at a
                    # shared address matches one of the WebMD dentist names,
                    # that is the practice - not a coin flip.
                    named = [c for c in cands
                             if any(fuzz.token_set_ratio(nz.name_seq(d),
                                                         nz.name_seq(maps_keep[c]["name"])) >= 85
                                    for d in wp["doctors"])]
                    if len(named) == 1:
                        confirmed = named
                if len(confirmed) == 1:
                    hit = confirmed[0]
                else:
                    ambiguous.append({"addr_key": wp["addr_key"],
                                      "candidates": cands,
                                      "doctors": wp["doctors"]})
                    continue
        if hit:
            attached.setdefault(hit, {"doctors": [], "profiles": []})
            attached[hit]["doctors"].extend(wp["doctors"])
            attached[hit]["profiles"].extend(wp["profiles"])
        else:
            if (not args.restrict_webmd_to_target_zips) or wp["zip"] in target_zips:
                unmatched.append(wp)

    rows = []
    for pid, r in maps_keep.items():
        att = attached.get(pid)
        docs = sorted(set(att["doctors"])) if att else []
        rows.append({
            "lead_id": pid[:16],
            "name": r["name"], "address": r["address"], "street": r["street"],
            "city": r["city"], "state": r["state"], "zip": nz.norm_zip(r["zip"]),
            "phone": nz.norm_phone(r["phone"]), "website": r["website"],
            "rating": r["rating"], "review_count": r["review_count"],
            "reviews_1star": r.get("reviews_1star", ""),
            "reviews_2star": r.get("reviews_2star", ""),
            "permanently_closed": r["permanently_closed"],
            "temporarily_closed": r["temporarily_closed"],
            "primary_category": r["primary_category"], "categories": r["categories"],
            "place_id": pid, "maps_url": r["maps_url"],
            "lat": r["lat"], "lng": r["lng"],
            "search_zip": r["search_zip"], "search_metro": r["search_metro"],
            "source": "Both" if att else "Google Maps",
            "maps_verified": "yes",
            "webmd_profiles": "|".join(sorted(set(att["profiles"]))) if att else "",
            "doctors": "|".join(docs), "doctor_count": len(docs),
            "provider": r["provider"], "scraped_at": r["scraped_at"],
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(rows)

    if maps_dupes:
        with REJ.open("w", newline="") as fh:
            cols = list(maps_dupes[0].keys())
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(maps_dupes)

    # WebMD practices with no Maps listing: candidates for targeted lookup
    pend = ROOT / "data" / "webmd_unmatched.json"
    pend.write_text(json.dumps([{k: (sorted(v) if isinstance(v, set) else v)
                                 for k, v in u.items()} for u in unmatched], indent=2))

    both = sum(1 for r in rows if r["source"] == "Both")
    rep = {
        "maps_raw": len(maps_rows), "maps_unique": len(maps_keep),
        "maps_duplicates": len(maps_dupes),
        "webmd_dentists": len(webmd_rows), "webmd_practices": len(wm_practices),
        "webmd_unusable_rows": len(wm_unusable),
        "webmd_matched_to_maps": both,
        "webmd_ambiguous_buildings": len(ambiguous),
        "webmd_only_no_maps_listing": len(unmatched),
        "merged_practices": len(rows),
        "practices_with_doctor_names": sum(1 for r in rows if r["doctors"]),
        "total_doctor_names": sum(r["doctor_count"] for r in rows),
    }
    REPORT.write_text(json.dumps({"summary": rep, "ambiguous": ambiguous[:50]},
                                 indent=2))
    print(json.dumps(rep, indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
