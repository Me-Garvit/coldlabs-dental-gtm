"""
Stage 1b - WebMD provider directory (doctor.webmd.com).

Chosen as the custom directory because it is public, national, lists individual
dentists (so it hands us decision-maker names for free) and is not a B2B
database, which the brief rules out.

Crawl path: specialty/state/city listing -> ?pagenumber=N -> provider cards.
Cards are server-rendered, so tier 1 of the fetcher (plain HTTP) handles them;
Camoufox only starts if WebMD puts up a bot wall.

Resumable: every page is cached to data/cache/webmd/. Re-runs are free.
Output: data/raw_webmd.csv, one row per dentist.
"""
import argparse
import csv
import html as htmllib
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetcher

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "webmd"
CACHE.mkdir(parents=True, exist_ok=True)
OUT = ROOT / "data" / "raw_webmd.csv"
BLOCKLOG = ROOT / "data" / "webmd_blocks.log"

BASE = "https://doctor.webmd.com/providers/specialty/dentistry"

CITIES = [
    ("texas", "houston", "Houston", "TX"),
    ("texas", "dallas", "Dallas", "TX"),
    ("florida", "tampa", "Tampa", "FL"),
    ("florida", "orlando", "Orlando", "FL"),
]

CARD_SPLIT = '<input type="hidden" value="https://doctor.webmd.com/doctor/'

FIELDS = ["doctor_name", "credentials", "specialty", "street", "city", "state",
          "zip", "address_full", "phone", "phone_raw", "profile_url",
          "search_city", "search_state", "page", "tier"]


def _txt(s):
    s = re.sub(r"<[^>]+>", "", s)
    return htmllib.unescape(s).replace(" ", " ").strip()


def _first(pattern, chunk, group=1):
    m = re.search(pattern, chunk, re.S)
    return _txt(m.group(group)) if m else ""


CRED_RE = re.compile(r",\s*((?:DDS|DMD|MD|DO|MS|MSD|PhD|FAGD|FICOI|BDS)(?:\s*,\s*\w+)*)\s*$", re.I)
ADDR_RE = re.compile(r"^(.*),\s*([^,]+),\s*([A-Z]{2}),\s*(\d{5})(?:-\d{4})?$")


def parse_page(html, search_city, search_state, page, tier):
    rows = []
    chunks = html.split(CARD_SPLIT)[1:]
    for ch in chunks:
        slug = ch.split('"', 1)[0]
        profile_url = f"https://doctor.webmd.com/doctor/{slug}"
        name = _first(r'class="prov-name"[^>]*>(.*?)</a>', ch)
        if not name:
            continue
        creds = ""
        m = CRED_RE.search(name)
        if m:
            creds = m.group(1).upper().replace(" ", "")
            name = name[:m.start()].strip()
        specialty = _first(r'class="prov-specialty"[^>]*>(.*?)</p>', ch)
        addr = _first(r'class="addr-text"[^>]*>(.*?)</span>', ch)
        phone_raw = _first(r'href="tel:([^"]+)"', ch, 1)
        phone = re.sub(r"\D", "", phone_raw)
        if len(phone) == 10:
            phone = "+1" + phone
        elif len(phone) == 11 and phone.startswith("1"):
            phone = "+" + phone
        else:
            phone = ""
        street = city = state = zc = ""
        am = ADDR_RE.match(addr)
        if am:
            street, city, state, zc = (am.group(1).strip(), am.group(2).strip(),
                                       am.group(3), am.group(4))
        rows.append({
            "doctor_name": name, "credentials": creds, "specialty": specialty,
            "street": street, "city": city, "state": state, "zip": zc,
            "address_full": addr, "phone": phone, "phone_raw": phone_raw,
            "profile_url": profile_url, "search_city": search_city,
            "search_state": search_state, "page": page, "tier": tier,
        })
    return rows


def last_page(html):
    pages = [int(n) for n in re.findall(r'pagenumber=(\d+)"[^>]*class="number"', html)]
    pages += [int(n) for n in re.findall(r'title="Page (\d+)"', html)]
    return max(pages) if pages else 1


def crawl_city(state_slug, city_slug, city, state, max_pages, target_zips):
    rows, pages_done, blocked = [], 0, 0
    total_pages = None
    page = 1
    while page <= max_pages:
        cfile = CACHE / f"{state_slug}_{city_slug}_p{page}.html"
        tier = "cache"
        if cfile.exists():
            html = cfile.read_text(encoding="utf-8", errors="replace")
        else:
            url = BASE + f"/{state_slug}/{city_slug}"
            if page > 1:
                url += f"?pagenumber={page}"
            try:
                html, tier, status = fetcher.fetch(url, wait_selector=".prov-name")
            except fetcher.Blocked as exc:
                blocked += 1
                with BLOCKLOG.open("a") as fh:
                    fh.write(f"{city} p{page}: {exc}\n")
                print(f"    BLOCKED {city} p{page}: {exc}")
                if blocked >= 3:
                    print(f"    giving up on {city} after 3 blocks")
                    break
                page += 1
                continue
            cfile.write_text(html, encoding="utf-8")
            fetcher.polite_sleep(2.0, 5.0)

        if total_pages is None:
            total_pages = last_page(html)
            print(f"  {city}: WebMD reports {total_pages} pages")
        page_rows = parse_page(html, city, state, page, tier)
        if not page_rows:
            print(f"    {city} p{page}: 0 cards, stopping")
            break
        rows.extend(page_rows)
        hits = sum(1 for r in page_rows if r["zip"] in target_zips)
        pages_done += 1
        print(f"    {city} p{page}: {len(page_rows)} dentists "
              f"({hits} in target ZIPs) [{tier}]")
        if total_pages and page >= total_pages:
            break
        page += 1
    return rows, pages_done, total_pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=45,
                    help="pages per city (each page ~65 dentists)")
    ap.add_argument("--zips", default=str(ROOT / "scripts/config/zips.csv"))
    args = ap.parse_args()

    target_zips = set()
    with open(args.zips) as fh:
        for r in csv.DictReader(fh):
            target_zips.add(r["zip"])

    all_rows, summary = [], []
    for state_slug, city_slug, city, state in CITIES:
        print(f"\n== {city}, {state} ==")
        rows, pages, total = crawl_city(state_slug, city_slug, city, state,
                                        args.max_pages, target_zips)
        all_rows.extend(rows)
        summary.append({"city": city, "state": state, "pages_crawled": pages,
                        "pages_available": total, "dentists": len(rows)})

    # de-dup by profile URL (featured cards repeat across pages)
    seen, rows = set(), []
    for r in all_rows:
        if r["profile_url"] in seen:
            continue
        seen.add(r["profile_url"])
        rows.append(r)

    with OUT.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    in_target = sum(1 for r in rows if r["zip"] in target_zips)
    print(f"\nwrote {len(rows)} unique dentists -> {OUT}")
    print(f"  {in_target} of them sit in our {len(target_zips)} target ZIPs")
    print(f"  {len(all_rows)-len(rows)} duplicate cards dropped")
    (ROOT / "data" / "webmd_stats.json").write_text(
        json.dumps({"cities": summary, "unique_dentists": len(rows),
                    "in_target_zips": in_target}, indent=2))
    fetcher.close_browser()


if __name__ == "__main__":
    main()
