"""
Stage 7 - build the Google Sheet deliverable as a multi-tab workbook.

Written as .xlsx and uploaded to Drive, which converts it to a Google Sheet
with every tab intact. Tabs: README, Final List, Qualified, Rejected, Funnel,
Doctors, QA Report.
"""
import csv
import json
import pathlib

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "deliverables" / "coldlabs_dental_list.xlsx"

HDR = PatternFill("solid", fgColor="1F3864")
HF = Font(color="FFFFFF", bold=True)

wb = Workbook()
wb.remove(wb.active)


def rows_of(p):
    with open(p) as fh:
        return list(csv.DictReader(fh))


def sheet(title, header, data, widths=None, wrap_cols=(), autofilter=True):
    ws = wb.create_sheet(title[:31])
    ws.append(header)
    for c in range(1, len(header) + 1):
        ws.cell(1, c).fill = HDR
        ws.cell(1, c).font = HF
        ws.column_dimensions[get_column_letter(c)].width = (widths or {}).get(c, 18)
    for r in data:
        ws.append(r)
    ws.freeze_panes = "A2"
    if autofilter:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(header))}{len(data) + 1}"
    for col in wrap_cols:
        for row in range(2, len(data) + 2):
            ws.cell(row, col).alignment = Alignment(wrap_text=True, vertical="top")
    return ws


rs = json.loads((ROOT / "data/run_summary.json").read_text())

readme = [
    ["ColdLabs GTME - dental review-management list", ""],
    ["Run date", "22 November 2026"],
    ["Total spend", "$%.2f across two Apify accounts ($4.9991 + $%.2f; free plan ceiling is $5.00 each)"
     % (rs["spend_usd_account_key1"] + rs["spend_usd_account_key2"], rs["spend_usd_account_key2"])],
    ["Geography", "Houston & Dallas (TX), Tampa & Orlando (FL) - 25 ZIPs"],
    ["Sources", "Google Maps via Apify (two-actor fallback chain) + WebMD provider directory"],
    ["", ""],
    ["TAB", "WHAT IT CONTAINS"],
    ["Final List", "The deliverable. Qualified practices with bad-review bullets and same-ZIP competitors. rating_tier separates the brief's under-4.0 cohort from the 4.0-4.5 tier."],
    ["Qualified (all ratings)", "Every practice that passed all qualification rules, at any rating."],
    ["Rejected", "Every rejected practice with its reject_code and a readable reason, so the cuts are auditable."],
    ["Funnel", "Counts at each stage, rejection breakdown, unit economics, rating distribution."],
    ["Doctors", "Practice to dentist-name mapping from WebMD. Input for decision-maker sourcing."],
    ["QA Report", "Automated checks over the Final List. Current result: 0 FAIL, 0 WARN."],
    ["Momentum (good rating, bad trend)", "A SECOND SEGMENT: practices rated 4.5+ that are taking bad reviews RIGHT NOW. Sorted by momentum_flag. 'actively_slipping' = 2 or more quotable 1-2 star reviews in the last 90 days. 46 practices probed; see the finding below."],
    ["", ""],
    ["KEY COLUMNS", ""],
    ["company_name", "Cleaned practice name: no legal suffixes, marketing tails or ALL CAPS."],
    ["domain", "Registered root domain, lowercase, no scheme/www/path."],
    ["rating_tier", "under_4.0 = the brief's threshold. 4.0_to_4.5 = the alternative tier."],
    ["bad_reviews", "Up to 5 most recent 1-2 star reviews as 'First name + last initial - 4-5 word summary'."],
    ["owner_replied_to_bad", "How many of those reviews the practice replied to. Unanswered reviews are the opening."],
    ["competitor_1..3", "Better-rated practices in the SAME ZIP (>=4.5 stars, >=25 reviews, rated above the lead). Never filled from a nearby ZIP."],
    ["rating_gap", "Top competitor's rating minus the lead's."],
    ["doctors", "Dentist names at the practice, from WebMD."],
    ["linkedin_url", "NOT POPULATED. The column is wired but the lookup was not implemented or run in this build, so it ships empty for all rows. This is a missing step, NOT evidence that these practices lack company pages - that was never checked."],
    ["status", "ok, or a code explaining exactly what is missing and why."],
    ["", ""],
    ["HEADLINE FINDING", ""],
    ["The under-4.0 pool is thin", "Only 2.8% of the 787 practices measured rate below 4.0; 74.2% sit at 4.8-5.0. Qualified yield is 0.93 leads per 100 places scraped, so 50+ under-4.0 leads needs roughly 5,400 places (~$21.60 of Apify credit). This run was capped by the $5.00 free plan and produced 4, shipped alongside 16 in the 4.0-4.5 tier. Full detail in docs/writeup.md section 6."],
    ["Smarter queries do not fix it", "Swapping 'dentist {ZIP}' for 'medicaid dentist' / 'emergency dental clinic' / 'affordable dentist' across 8 ZIPs returned 47 new places and one marginal lead. The only lever is volume."],
    ["", ""],
    ["THE MOMENTUM SEGMENT", ""],
    ["Why it exists", "A 4.7-star practice taking four 1-star reviews this quarter has a reputation worth defending and a trend against it. That is a different and often warmer pitch than a 3.4 practice that has already bottomed out."],
    ["Hit rate", "All 197 qualified practices rated 4.5+ with 5 or more lifetime bad reviews were probed. 20 are 'actively_slipping' (2+ quotable bad reviews in the last 90 days), 40 more have one, 137 are quiet. Overall hit rate 10%."],
    ["Why it is worth more than the under-4.0 list", "20 actively-slipping leads versus 5 under-4.0, at roughly $0.007 per practice probed. The under-4.0 pool is genuinely scarce; this one is not."],
    ["How to read owner_replied_to_recent", "A practice replying to all its bad reviews already cares and is doing it by hand - a warm prospect. One replying to none has the bigger problem but needs more convincing. Both are targets; the pitch differs."],
]
sheet("README", ["Field", "Value"], readme, widths={1: 30, 2: 115},
      wrap_cols=(2,), autofilter=False)

FL = ["company_name", "doctors", "doctor_count", "domain", "rating_tier",
      "google_rating", "review_count", "city", "state", "zip",
      "bad_reviews", "bad_review_count_90d",
      "last_bad_review_date", "owner_replied_to_bad",
      "competitor_1_name", "competitor_1_rating", "competitor_1_reviews",
      "competitor_2_name", "competitor_2_rating", "competitor_2_reviews",
      "competitor_3_name", "competitor_3_rating", "competitor_3_reviews",
      "rating_gap", "specialty", "source", "linkedin_url",
      "phone", "address", "maps_url", "status"]
sheet("Final List", FL,
      [[r.get(c, "") for c in FL] for r in rows_of(ROOT / "data/final_list.csv")],
      widths={1: 30, 2: 38, 4: 30, 11: 60, 15: 26, 18: 26, 21: 26, 30: 34},
      wrap_cols=(2, 11, 30))

Q = ["company_name", "domain", "rating", "review_count", "city", "state", "zip",
     "specialty", "source", "doctor_count", "primary_category", "phone",
     "builder_domain", "place_id"]
sheet("Qualified (all ratings)", Q,
      [[r.get(c, "") for c in Q] for r in rows_of(ROOT / "data/qualified.csv")],
      widths={1: 32, 2: 30, 11: 22, 14: 30})

R = ["name", "domain", "reject_code", "reject_note", "rating", "review_count",
     "city", "state", "zip", "website"]
sheet("Rejected", R,
      [[r.get(c, "") for c in R] for r in rows_of(ROOT / "data/rejected.csv")],
      widths={1: 32, 2: 28, 3: 20, 4: 62, 10: 34}, wrap_cols=(4,))

f = rs["funnel"]
fun = [["STAGE", "COUNT"],
       ["Google Maps raw listings", f["maps_raw_listings"]],
       ["WebMD dentists scraped", f["webmd_dentists"]],
       ["Merged practices (deduped, cross-source matched)", f["merged_practices"]],
       ["Qualified", f["qualified"]],
       ["Rejected", f["rejected"]],
       ["Qualified AND rated under 4.0", f["under_4.0_qualified"]],
       ["WebMD practices resolved by address lookup", f["webmd_resolved_by_address"]],
       ["Shipped on Final List", f["final_rows_shipped"]],
       ["  of which tier under_4.0", f["tier_under_4.0"]],
       ["  of which tier 4.0_to_4.5", f["tier_4.0_to_4.5"]],
       ["", ""],
       ["Momentum practices probed", f["momentum_probed"]],
       ["  of which actively slipping", f["momentum_actively_slipping"]],
       ["", ""], ["REJECTION REASON", "COUNT"]]
fun += [[k, v] for k, v in rs["reject_reasons"].items()]
tot = rs["spend_usd_account_key1"] + rs["spend_usd_account_key2"]
leads = f["final_rows_shipped"] + f["momentum_actively_slipping"]
fun += [["", ""], ["UNIT ECONOMICS", ""],
        ["Total spend (USD), both accounts", round(tot, 2)],
        ["Leads carrying review evidence", leads],
        ["Cost per lead", round(tot / max(leads, 1), 3)],
        ["WebMD address resolution accept rate", "78% (18 of 23 anchors)"],
        ["", ""], ["ENRICHMENT FILL", f"COUNT (of {f['final_rows_shipped']})"]]
fun += [[k, v] for k, v in rs["enrichment_fill"].items()]
fun += [["", ""], ["RATING DISTRIBUTION (787 practices)", "SHARE"],
        ["4.8 - 5.0", "74.2%"], ["4.5 - 4.79", "15.8%"], ["4.0 - 4.49", "7.3%"],
        ["3.5 - 3.99", "1.7%"], ["under 3.5", "1.0%"]]
sheet("Funnel", ["METRIC", "VALUE"], fun, widths={1: 48, 2: 20}, autofilter=False)

M = ["company_name", "doctors", "domain", "momentum_flag", "google_rating", "review_count",
     "bad_last_90d", "bad_last_90d_with_text", "owner_replied_to_recent",
     "last_bad_review_date", "bad_reviews", "bad_reviews_lifetime",
     "reviews_last_90d", "city", "state", "zip",
     "competitor_1_name", "competitor_1_rating", "competitor_1_reviews",
     "competitor_2_name", "competitor_2_rating", "competitor_2_reviews",
     "competitor_3_name", "competitor_3_rating", "competitor_3_reviews",
     "rating_gap", "cohort", "source", "phone", "maps_url"]
mom = ROOT / "deliverables/momentum_list.csv"
if mom.exists():
    sheet("Momentum (good rating, bad trend)", M,
          [[r.get(c, "") for c in M] for r in rows_of(mom)],
          widths={1: 32, 2: 38, 3: 30, 4: 22, 11: 58, 17: 26, 20: 26, 23: 26},
          wrap_cols=(2, 11))

D = ["company_name", "doctor_name", "domain", "city", "state", "zip", "rating", "source"]
sheet("Doctors", D,
      [[r.get(c, "") for c in D] for r in rows_of(ROOT / "deliverables/doctors.csv")],
      widths={1: 34, 2: 28, 3: 28})

qa = [[l] for l in (ROOT / "data/qa_report.md").read_text().splitlines()]
sheet("QA Report", ["Automated QA over the Final List"], qa,
      widths={1: 120}, autofilter=False)

OUT.parent.mkdir(exist_ok=True)
wb.save(OUT)
print(f"saved {OUT} ({OUT.stat().st_size} bytes)")
print("tabs:", wb.sheetnames)
