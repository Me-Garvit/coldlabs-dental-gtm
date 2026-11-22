"""
Automated QA. Runs on every build of the final list and writes data/qa_report.md
plus data/qa_report.json. A FAIL means the row should not ship.
"""
import csv
import json
import pathlib
import re
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import normalize as nz
from clean_enrich import clean_name

ROOT = pathlib.Path(__file__).resolve().parent.parent
BLOCK = [l.strip().lower() for l in
         (ROOT / "scripts/config/dso_blocklist.txt").read_text().splitlines()
         if l.strip() and not l.startswith("#")]

BULLET = re.compile(r"^- (?P<who>[A-Z][\w.'’-]*(?:\s+[A-Z]\.)?)\s+-\s+(?P<sum>.+)$")
BAD_NAME_TOKENS = re.compile(
    r"(\||\bllc\b|\bpllc\b|\bp\.?c\.?\b|\bp\.?a\.?\b|\binc\b|\bbest\b|"
    r"\bnear me\b|\bdentist in\b|\d{3}[-.\s]?\d{3}[-.\s]?\d{4})", re.I)


def check(rows, pool_by_zip, max_rating=4.0):
    issues = []

    def fail(sev, rule, lead, detail):
        issues.append({"severity": sev, "rule": rule,
                       "lead": lead, "detail": detail})

    # --- uniqueness ---
    # A practice with two or three locations legitimately shares one domain
    # and one phone across them - that is why multi_location only cuts above
    # three. So a repeat is only a defect when it is the SAME listing twice
    # (same place_id) or the same site in the same ZIP.
    for v, n in Counter(r.get("place_id", "") for r in rows if r.get("place_id")).items():
        if n > 1:
            fail("FAIL", "unique_place_id", v, f"same listing appears {n} times")
    for field in ("domain", "phone"):
        pairs = [(r.get(field, ""), nz.norm_zip(r.get("zip")))
                 for r in rows if r.get(field)]
        for (v, z), n in Counter(pairs).items():
            if n > 1:
                fail("FAIL", "unique_" + field, v,
                     f"appears {n} times in the same ZIP ({z})")
        spread = Counter(v for v, _ in pairs)
        for v, n in spread.items():
            if n > 3:
                fail("FAIL", "multi_location_" + field, v,
                     f"{n} locations share this {field} - over the 3-location limit")

    for r in rows:
        lead = r.get("company_name") or r.get("name") or r.get("domain")
        d = r.get("domain", "")

        # --- domain hygiene ---
        if not d:
            fail("FAIL", "domain_present", lead, "empty domain")
        else:
            if re.search(r"https?:|^www\.|/|\?", d):
                fail("FAIL", "domain_clean", lead, f"{d!r} is not a bare root domain")
            if nz.is_directory_domain(d):
                fail("FAIL", "domain_not_directory", lead, f"{d} is a directory/social profile")

        # --- chain recheck ---
        hay = f"{(r.get('company_name') or '').lower()} {d}"
        for tok in BLOCK:
            if tok in hay:
                fail("FAIL", "no_chains", lead, f"blocklist token {tok!r} survived")
                break

        # --- name hygiene ---
        nm = r.get("company_name", "")
        if not nm:
            fail("FAIL", "name_present", lead, "empty company_name")
        else:
            m = BAD_NAME_TOKENS.search(nm)
            if m:
                fail("FAIL", "name_clean", lead, f"{nm!r} still contains {m.group(0)!r}")
            if nm.isupper() and len(nm) > 3:
                fail("WARN", "name_case", lead, f"{nm!r} is ALL CAPS")
            if len(nm.split()) > 6:
                fail("WARN", "name_length", lead, f"{nm!r} is {len(nm.split())} words")

        # --- rating filter ---
        try:
            rating = float(r.get("google_rating") or r.get("rating") or 0)
        except ValueError:
            rating = 0
        if not (0 < rating < max_rating):
            fail("FAIL", "rating_threshold", lead,
                 f"rating {rating} is not under {max_rating}")
        try:
            rc = int(float(r.get("review_count") or 0))
            if rc < 10:
                fail("FAIL", "min_reviews", lead, f"only {rc} reviews")
        except ValueError:
            fail("WARN", "min_reviews", lead, "unparseable review_count")

        # --- review bullets ---
        bullets = [b for b in (r.get("bad_reviews") or "").split("\n") if b.strip()]
        status = r.get("status") or ""
        if not bullets and not any(c in status for c in
                                   ("no_bad_reviews", "listing_not_found")):
            fail("WARN", "bad_reviews_present", lead, "no bullets and status does not say why")
        for b in bullets:
            m = BULLET.match(b.strip())
            if not m:
                fail("FAIL", "bullet_format", lead, f"{b[:70]!r} is not 'Name L. - summary'")
                continue
            words = m.group("sum").split()
            if not (3 <= len(words) <= 6):
                fail("WARN", "bullet_length", lead,
                     f"summary is {len(words)} words: {m.group('sum')!r}")
            if re.search(r"\b(hiv|cancer|pregnan|diabet|surgery scheduled|diagnos)\w*",
                         m.group("sum"), re.I):
                fail("FAIL", "bullet_phi", lead, f"possible health detail: {m.group('sum')!r}")

        # --- competitors ---
        lead_zip = nz.norm_zip(r.get("zip"))
        for i in (1, 2, 3):
            cname = r.get(f"competitor_{i}_name")
            if not cname:
                continue
            try:
                crate = float(r.get(f"competitor_{i}_rating") or 0)
                creviews = int(float(r.get(f"competitor_{i}_reviews") or 0))
            except ValueError:
                fail("FAIL", "competitor_numbers", lead, f"competitor {i} has unparseable figures")
                continue
            if crate < 4.5:
                fail("FAIL", "competitor_rating", lead, f"{cname} rated {crate} (<4.5)")
            if crate <= rating:
                fail("FAIL", "competitor_better", lead, f"{cname} ({crate}) not better than lead ({rating})")
            if creviews < 25:
                fail("FAIL", "competitor_reviews", lead, f"{cname} has {creviews} reviews (<25)")
            # the shipped competitor name is cleaned, so clean the pool side
            # too before comparing - otherwise this check can never match.
            want = nz.name_key(cname)
            match = [p for p in pool_by_zip.get(lead_zip, [])
                     if nz.name_key(clean_name(p.get("name", ""))[0]) == want
                     or nz.name_key(p.get("name", "")) == want]
            if not match:
                fail("WARN", "competitor_zip", lead,
                     f"{cname} not found in ZIP {lead_zip} pool")

    return issues


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?", default=str(ROOT / "data" / "final_list.csv"))
    ap.add_argument("--max-rating", type=float, default=4.0)
    a = ap.parse_args()
    src = pathlib.Path(a.src)
    with src.open() as fh:
        rows = list(csv.DictReader(fh))

    pool = {}
    pcsv = ROOT / "data" / "raw_maps.csv"
    if pcsv.exists():
        with pcsv.open() as fh:
            for r in csv.DictReader(fh):
                pool.setdefault(nz.norm_zip(r.get("zip")), []).append(r)

    issues = check(rows, pool, a.max_rating)
    fails = [i for i in issues if i["severity"] == "FAIL"]
    warns = [i for i in issues if i["severity"] == "WARN"]

    by_rule = Counter(i["rule"] for i in issues)
    lines = [f"# QA report - {src.name}", "",
             f"- rows checked: **{len(rows)}**",
             f"- FAIL: **{len(fails)}**", f"- WARN: **{len(warns)}**", ""]
    if issues:
        lines += ["## By rule", "", "| rule | severity | count |", "|---|---|---|"]
        for rule, n in by_rule.most_common():
            sev = "FAIL" if any(i["rule"] == rule and i["severity"] == "FAIL"
                                for i in issues) else "WARN"
            lines.append(f"| {rule} | {sev} | {n} |")
        lines += ["", "## Detail (first 60)", ""]
        for i in issues[:60]:
            lines.append(f"- **{i['severity']}** `{i['rule']}` - {i['lead']}: {i['detail']}")
    else:
        lines.append("No issues found.")

    # before/after name sample for spot review
    sample = [r for r in rows if r.get("name_raw") and
              r.get("name_raw") != r.get("company_name")][:15]
    if sample:
        lines += ["", "## Name cleaning: before -> after", "",
                  "| raw Google Maps name | cleaned |", "|---|---|"]
        for r in sample:
            lines.append(f"| {r['name_raw']} | {r['company_name']} |")

    (ROOT / "data" / "qa_report.md").write_text("\n".join(lines) + "\n")
    (ROOT / "data" / "qa_report.json").write_text(json.dumps(issues, indent=2))
    print("\n".join(lines[:14]))
    print(f"\nwrote data/qa_report.md ({len(fails)} FAIL, {len(warns)} WARN)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
