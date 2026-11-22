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
    ["Total spend", "$%.2f (Apify free plan, $5.00/month ceiling)" % rs["spend_usd_total"]],
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
]
sheet("README", ["Field", "Value"], readme, widths={1: 30, 2: 115},
      wrap_cols=(2,), autofilter=False)

FL = ["company_name", "domain", "rating_tier", "google_rating", "review_count",
      "city", "state", "zip", "bad_reviews", "bad_review_count_90d",
      "last_bad_review_date", "owner_replied_to_bad",
      "competitor_1_name", "competitor_1_rating", "competitor_1_reviews",
      "competitor_2_name", "competitor_2_rating", "competitor_2_reviews",
      "competitor_3_name", "competitor_3_rating", "competitor_3_reviews",
      "rating_gap", "doctors", "specialty", "source", "linkedin_url",
      "phone", "address", "maps_url", "status"]
sheet("Final List", FL,
      [[r.get(c, "") for c in FL] for r in rows_of(ROOT / "data/final_list.csv")],
      widths={1: 30, 2: 30, 9: 60, 13: 26, 16: 26, 19: 26, 23: 34, 28: 34, 29: 30},
      wrap_cols=(9, 23, 28))

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
       ["Shipped on Final List", f["final_rows_shipped"]],
       ["  of which tier under_4.0", f["tier_under_4.0"]],
       ["  of which tier 4.0_to_4.5", f["tier_4.0_to_4.5"]],
       ["", ""], ["REJECTION REASON", "COUNT"]]
fun += [[k, v] for k, v in rs["reject_reasons"].items()]
fun += [["", ""], ["UNIT ECONOMICS", ""],
        ["Total spend (USD)", rs["spend_usd_total"]],
        ["Cost per place scraped", rs["cost_per_place_scraped"]],
        ["Cost per shipped lead", rs["cost_per_shipped_lead"]],
        ["Cost per qualified under-4.0 lead", rs["cost_per_qualified_lead_under_4.0"]],
        ["", ""], ["ENRICHMENT FILL", "COUNT (of 20)"]]
fun += [[k, v] for k, v in rs["enrichment_fill"].items()]
fun += [["", ""], ["RATING DISTRIBUTION (787 practices)", "SHARE"],
        ["4.8 - 5.0", "74.2%"], ["4.5 - 4.79", "15.8%"], ["4.0 - 4.49", "7.3%"],
        ["3.5 - 3.99", "1.7%"], ["under 3.5", "1.0%"]]
sheet("Funnel", ["METRIC", "VALUE"], fun, widths={1: 48, 2: 20}, autofilter=False)

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
