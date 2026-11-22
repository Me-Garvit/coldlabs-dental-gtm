"""
Stage 3 - qualification. Every rejected row keeps a reject_code and survives
into the rejected tab, so the merge and the cuts can be audited.

Rules run in this order and the FIRST failure is the one recorded:

  O1 not_a_practice      labs, supply stores, schools-as-businesses
  O2 institutional       dental schools, hospital depts, FQHCs, VA, mobile vans
  O4 out_of_geo          Maps returns neighbours outside the searched ZIPs
  B2 closed              permanently / temporarily closed
  B1 dso_chain           blocklist name/domain, DSO footer text, DSO redirect
  B4 no_website          blank, social/directory only, dead, or parked
  B5 multi_location_gt3  >3 Maps listings on one domain or phone, or a
                         locations page covering >3 ZIPs
  O3 low_review_count    fewer than 10 Google reviews
  O5 domain_mismatch     the site belongs to a different practice

Specialists (ortho, oral surgery, endo, perio) are NOT rejected - they live and
die by reviews too. They are flagged in `specialty` so copy can adapt.
"""
import argparse
import csv
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict

from rapidfuzz import fuzz

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import normalize as nz

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "scripts/config/categories.json").read_text())
BLOCK = [l.strip().lower() for l in
         (ROOT / "scripts/config/dso_blocklist.txt").read_text().splitlines()
         if l.strip() and not l.startswith("#")]

MERGED = ROOT / "data" / "merged.csv"
PROBES = ROOT / "data" / "site_probes.json"
QUAL = ROOT / "data" / "qualified.csv"
REJ = ROOT / "data" / "rejected.csv"
FUNNEL = ROOT / "data" / "funnel.json"

MIN_REVIEWS = 10


def cats_of(row):
    c = [row.get("primary_category", "")] + (row.get("categories", "") or "").split("|")
    return {x.strip().lower() for x in c if x.strip()}


def check_not_a_practice(row, ctx):
    cats = cats_of(row)
    name = (row["name"] or "").lower()
    if cats & set(CFG["not_a_practice_categories"]):
        bad = sorted(cats & set(CFG["not_a_practice_categories"]))
        return f"category: {bad[0]}"
    for tok in CFG["not_a_practice_name_tokens"]:
        if tok in name:
            return f"name token: {tok}"
    # nothing dental about it at all - but let the institutional rule claim it
    # first, because "institutional" is the more informative reject code.
    if not (cats & set(CFG["practice_categories"])) and \
       not re.search(r"dent|orthodon|endodon|periodon|prosthodon|oral surg|smile|tooth|teeth",
                     name):
        if check_institutional(row, ctx):
            return None
        return f"no dental category or name signal ({row.get('primary_category','?')})"
    return None


def check_institutional(row, ctx):
    name = (row["name"] or "").lower()
    cats = cats_of(row)
    for tok in CFG["institutional_name_tokens"]:
        if tok in name:
            return f"name token: {tok}"
    if cats & set(CFG["institutional_categories"]):
        return f"category: {sorted(cats & set(CFG['institutional_categories']))[0]}"
    return None


MIN_POOL_FOR_COMPETITORS = 8


def check_out_of_geo(row, ctx):
    """
    Geography is gated on *competitor coverage*, not on the grid list.

    A ZIP query pulls in neighbouring ZIPs too. Those are real practices in the
    same metro, so cutting them purely for not being on the list throws away
    good leads. What actually disqualifies a lead is that we cannot find its
    same-ZIP competitors - and the honest test for that is how many places we
    hold in its ZIP, not whether we typed that ZIP into the grid.
    """
    z = nz.norm_zip(row["zip"])
    if not z:
        return "no ZIP on the Maps listing"
    if (row.get("state") or "").upper() not in ctx["target_states"]:
        return f"{row.get('state') or '??'} is outside the target states"
    if z in ctx["target_zips"]:
        return None
    pool = ctx["zip_pool_counts"].get(z, 0)
    if pool < MIN_POOL_FOR_COMPETITORS:
        return (f"ZIP {z} is off-grid and we hold only {pool} listing(s) there "
                f"- too thin to source same-ZIP competitors")
    return None


def check_closed(row, ctx):
    if str(row.get("permanently_closed", "")).lower() == "true":
        return "permanently closed on Google"
    if str(row.get("temporarily_closed", "")).lower() == "true":
        return "temporarily closed on Google"
    return None


SELF_REF = re.compile(r"(?:©\s*\d{4}|part of the|a member of the)\s+(.{3,50}?)"
                      r"(?:\s+(?:family|network|group|partners|management))?$", re.I)


def _self_referential(hit, practice_name):
    """
    "© 2019 Rizzo Dental Group" on rizzodentalgroup.com is the practice's own
    copyright line, not a parent DSO. Only an org name that is NOT the practice
    counts as evidence.
    """
    m = SELF_REF.search(hit.strip())
    if not m:
        return False
    return fuzz.partial_token_set_ratio(nz.name_seq(m.group(1)),
                                        nz.name_seq(practice_name)) >= 80


def check_dso(row, ctx):
    name = (row["name"] or "").lower()
    dom = ctx["domain_of"].get(row["lead_id"], "")
    probe = ctx["probe_of"].get(row["lead_id"]) or {}
    hay = f"{name} {dom}"
    for token in BLOCK:
        if token in hay:
            return f"blocklist match: {token!r}"
    final_dom = probe.get("final_domain", "")
    if final_dom and dom and final_dom != dom:
        for token in BLOCK:
            if token in final_dom:
                return f"site redirects to DSO domain {final_dom}"
    hits = [h for h in (probe.get("dso_hits") or [])
            if not _self_referential(h, row["name"])]
    if hits:
        return f"DSO phrasing on site: {hits[0]!r}"
    return None


def check_no_website(row, ctx):
    raw = (row["website"] or "").strip()
    if not raw:
        return "no website on the Maps listing"
    dom = ctx["domain_of"].get(row["lead_id"], "")
    if not dom:
        return f"unparseable website {raw[:60]!r}"
    if nz.is_directory_domain(dom):
        return f"social/directory profile only ({dom})"
    probe = ctx["probe_of"].get(row["lead_id"]) or {}
    if not probe:
        return None                      # never probed; do not punish the row
    if probe.get("parked"):
        return f"parked domain ({dom})"
    dead_title = re.search(r"temporarily unavailable|account suspended|"
                           r"site not found|domain (is )?for sale|under construction",
                           (probe.get("title") or ""), re.I)
    if dead_title:
        return f"site is not live ({dom}: {probe.get('title','')[:45]!r})"
    if not probe.get("dns_ok"):
        return f"domain does not resolve ({dom})"
    if probe.get("blocked"):
        # The domain resolves and serves content to a browser; a bot wall is
        # not evidence of a missing website. Keep the lead, flag it.
        return None
    if not probe.get("ok"):
        return f"site unreachable ({dom}: {probe.get('error') or 'no response'})"
    return None


def check_multi_location(row, ctx):
    dom = ctx["domain_of"].get(row["lead_id"], "")
    if dom and not nz.is_builder_domain(dom):
        n = ctx["domain_counts"].get(dom, 0)
        if n > 3:
            return f"{n} Maps listings share the domain {dom}"
    ph = row["phone"]
    if ph:
        n = ctx["phone_counts"].get(ph, 0)
        if n > 3:
            return f"{n} Maps listings share the phone {ph}"
    probe = ctx["probe_of"].get(row["lead_id"]) or {}
    if probe.get("has_locations_link"):
        zips = set(probe.get("zips_on_page") or [])
        zips.discard(nz.norm_zip(row["zip"]))
        if len(zips) > 3:
            return (f"locations page lists {len(zips)+1} ZIPs "
                    f"({', '.join(sorted(zips)[:4])}...)")
    return None


def check_low_reviews(row, ctx):
    try:
        n = int(float(row["review_count"] or 0))
    except ValueError:
        n = 0
    if n < MIN_REVIEWS:
        return f"only {n} Google reviews (need {MIN_REVIEWS})"
    return None


def check_domain_mismatch(row, ctx):
    probe = ctx["probe_of"].get(row["lead_id"]) or {}
    title = (probe.get("title") or "")
    if not title or not probe.get("ok"):
        return None
    if probe.get("redirect_shim") or probe.get("blocked"):
        return None                      # we never saw the real page
    sample = (probe.get("text_sample") or "")[:1200]
    nm = nz.name_seq(row["name"])
    if not nm:
        return None

    # A distinctive word from the practice name must appear on the page.
    # partial_token_set_ratio alone is not safe here: on a short name like
    # "A K Islam BDS" the one-letter tokens match almost any text and push the
    # score over any threshold. drakislam.com had lapsed and now redirects to
    # an online casino, and the fuzzy score alone waved it through.
    hay = f"{title} {sample}".lower()
    dental = re.search(r"dent(al|ist|istry)|orthodont|endodont|periodont|"
                       r"prosthodont|oral surg|smile|teeth|tooth|hygien|implant",
                       hay)
    distinctive = [t for t in nm.split() if len(t) >= 4]
    name_on_page = any(t in hay for t in distinctive) if distinctive else True

    # A dentist practising under a practice brand legitimately has a domain
    # that carries none of their own name - Robert Beckmann at
    # prestonsherrydental.com is a correct pairing, not a mismatch. So an
    # absent name only condemns a site that is not a dental site at all.
    # That is what catches the real failures: drakislam.com now redirects to
    # an online casino, and Rose Dental resolves to an errands company.
    if not name_on_page and not dental:
        return (f"{probe.get('final_domain') or 'the site'} is not a dental "
                f"site and carries no word from the practice name "
                f"(title {title[:45]!r})")
    # Deliberately nothing else. A fuzzy name-vs-title score was tried and
    # removed: it rejected JuanCarlosBarbosaDDS for owning
    # juancarlosbarbosa.com, and every dentist practising under a practice
    # brand. If the site is a dental site, it is the right domain to email,
    # whatever it is called.
    return None


RULES = [
    ("not_a_practice", check_not_a_practice),
    ("institutional", check_institutional),
    ("out_of_geo", check_out_of_geo),
    ("closed", check_closed),
    ("dso_chain", check_dso),
    ("no_website", check_no_website),
    ("multi_location_gt3", check_multi_location),
    ("low_review_count", check_low_reviews),
    ("domain_mismatch", check_domain_mismatch),
]


def main():
    global MIN_REVIEWS
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-reviews", type=int, default=MIN_REVIEWS)
    args = ap.parse_args()
    MIN_REVIEWS = args.min_reviews

    with MERGED.open() as fh:
        rows = list(csv.DictReader(fh))
    probes = json.loads(PROBES.read_text()) if PROBES.exists() else {}

    target_zips = {nz.norm_zip(r["search_zip"]) for r in rows} - {""}
    with (ROOT / "scripts/config/zips.csv").open() as fh:
        target_zips |= {r["zip"] for r in csv.DictReader(fh)}

    domain_of, probe_of = {}, {}
    for r in rows:
        d = nz.root_domain(r["website"])
        domain_of[r["lead_id"]] = d
        probe_of[r["lead_id"]] = probes.get(r["website"])

    domain_counts = Counter(d for d in domain_of.values()
                            if d and not nz.is_directory_domain(d))
    phone_counts = Counter(r["phone"] for r in rows if r["phone"])

    zip_pool_counts = Counter(nz.norm_zip(r["zip"]) for r in rows if r["zip"])
    target_states = {"TX", "FL"}

    ctx = {"target_zips": target_zips, "domain_of": domain_of,
           "probe_of": probe_of, "domain_counts": domain_counts,
           "phone_counts": phone_counts,
           "zip_pool_counts": zip_pool_counts, "target_states": target_states}

    qualified, rejected = [], []
    reasons = Counter()
    for r in rows:
        cats = cats_of(r)
        r["specialty"] = "|".join(sorted(cats & set(CFG["specialist_categories"]))) or "general"
        r["domain"] = domain_of[r["lead_id"]]
        code = note = None
        for rule_code, fn in RULES:
            note = fn(r, ctx)
            if note:
                code = rule_code
                break
        if code:
            reasons[code] += 1
            rejected.append({**r, "reject_code": code, "reject_note": note})
        else:
            qualified.append(r)

    fields = list(rows[0].keys()) + ["specialty", "domain"]
    fields = list(dict.fromkeys(fields))
    with QUAL.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(qualified)
    with REJ.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields + ["reject_code", "reject_note"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rejected)

    under4 = [r for r in qualified
              if r["rating"] not in ("", None) and float(r["rating"]) < 4.0]
    funnel = {
        "merged_practices": len(rows),
        "qualified": len(qualified),
        "rejected": len(rejected),
        "reject_reasons": dict(reasons.most_common()),
        "qualified_under_4.0": len(under4),
        "by_source": dict(Counter(r["source"] for r in rows)),
        "qualified_by_metro": dict(Counter(r["search_metro"] for r in qualified)),
        "under4_by_metro": dict(Counter(r["search_metro"] for r in under4)),
        "min_reviews_rule": MIN_REVIEWS,
    }
    FUNNEL.write_text(json.dumps(funnel, indent=2))
    print(json.dumps(funnel, indent=2))


if __name__ == "__main__":
    main()
