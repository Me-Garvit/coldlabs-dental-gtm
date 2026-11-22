"""
One HTTP pass per unique practice domain. Feeds four qualification rules at
once, so we never fetch the same small site twice:

  B4  no_website        - dead DNS / 4xx / 5xx / parked page
  B1b dso_chain         - "a Heartland Dental supported practice" style footer
                          text, and redirects onto a known DSO domain. Many
                          DSOs keep the local brand, so a name check alone
                          misses them.
  B5  multi_location    - distinct ZIPs / "locations" links on the site
  O5  domain_mismatch   - the site's title/brand belongs to another practice

Results cached to data/cache/sites/. Costs nothing but time.
"""
import concurrent.futures as cf
import socket
import hashlib
import html as htmllib
import json
import pathlib
import re
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import normalize as nz

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "sites"
CACHE.mkdir(parents=True, exist_ok=True)
OUT = ROOT / "data" / "site_probes.json"

DSO_PHRASES = [
    r"supported by\s+([A-Z][\w&.' -]{2,40})\s+(dental|dentistry|group|partners)",
    r"is a\s+([A-Z][\w&.' -]{2,40})\s+supported (practice|office)",
    r"(dental )?(practice|office)s? supported by",
    r"part of the\s+([A-Z][\w&.' -]{2,40})\s+(family|network|group)",
    r"a member of the\s+([A-Z][\w&.' -]{2,40})\s+(family|network|group)",
    r"©\s*\d{4}\s+([A-Z][\w&.' -]{2,40})\s+(dental partners|dental management|dental holdings|dso)",
    r"(dental support organization|dental service organization)",
    r"managed by\s+([A-Z][\w&.' -]{2,40})\s+dental (partners|group|management)",
]
PARKED = ["this domain is for sale", "buy this domain", "domain parking",
          "godaddy.com/forsale", "future home of", "sedoparking",
          "under construction", "account suspended", "default web site page",
          "coming soon", "hugedomains"]

LOC_LINK = re.compile(r'href="[^"]*(locations?|our-offices?|offices?)[^"]*"', re.I)
ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def visible_text(html):
    h = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    h = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", htmllib.unescape(h))


REDIRECT_SHIM = re.compile(
    r"(you are being redirected|just a moment|redirecting|please wait|"
    r"enable javascript|checking your browser)", re.I)


def dns_resolves(host):
    """The authoritative 'does this domain exist' test. A WAF can 403 us; a
    domain that does not resolve is genuinely dead."""
    try:
        socket.getaddrinfo(host, None)
        return True
    except (socket.gaierror, UnicodeError, ValueError):
        return False


def probe_one(url):
    key = hashlib.sha256(url.encode()).hexdigest()[:20]
    cfile = CACHE / f"{key}.json"
    if cfile.exists():
        return json.loads(cfile.read_text())

    rec = {"input_url": url, "ok": False, "status": None, "final_url": "",
           "final_domain": "", "title": "", "dead": True, "parked": False,
           "blocked": False, "dns_ok": False, "redirect_shim": False,
           "dso_hits": [], "zips_on_page": [], "has_locations_link": False,
           "error": ""}
    host = nz.root_domain(url)
    rec["dns_ok"] = dns_resolves(host) if host else False
    try:
        r = requests.get(url if url.startswith("http") else "http://" + url,
                         timeout=20, allow_redirects=True,
                         headers={"User-Agent": UA,
                                  "Accept-Language": "en-US,en;q=0.9"})
        rec["status"] = r.status_code
        rec["final_url"] = r.url
        rec["final_domain"] = nz.root_domain(r.url)
        body = r.text or ""
        m = TITLE_RE.search(body)
        rec["title"] = re.sub(r"\s+", " ", htmllib.unescape(m.group(1))).strip()[:200] if m else ""
        rec["redirect_shim"] = bool(REDIRECT_SHIM.search(rec["title"]))
        # 401/403/429 with a real body is a WAF turning away our scraper, not a
        # practice without a website. We do not try to dress around it (see
        # fetcher.py), but we must not call the site dead either.
        if r.status_code in (401, 403, 429) and len(body) > 500:
            rec["blocked"] = True
            rec["dead"] = False
            rec["error"] = f"WAF/bot wall: HTTP {r.status_code}"
        elif 200 <= r.status_code < 400 and len(body) > 500:
            rec["ok"], rec["dead"] = True, False
            text = visible_text(body)
            low = text.lower()
            rec["parked"] = any(p in low for p in PARKED)
            for pat in DSO_PHRASES:
                for mm in re.finditer(pat, text, re.I):
                    rec["dso_hits"].append(mm.group(0).strip()[:120])
            rec["dso_hits"] = sorted(set(rec["dso_hits"]))[:5]
            rec["zips_on_page"] = sorted(set(ZIP_RE.findall(text)))[:30]
            rec["has_locations_link"] = bool(LOC_LINK.search(body))
            rec["text_sample"] = text[:1500]
        else:
            rec["error"] = f"status {r.status_code} / {len(body)}B"
    except requests.RequestException as exc:
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        # DNS resolves but the fetch failed: the domain is registered and
        # pointed somewhere, so this is a transport problem, not a dead site.
        if rec["dns_ok"]:
            rec["dead"] = False
            rec["blocked"] = True

    cfile.write_text(json.dumps(rec))
    return rec


def probe_all(urls, workers=12, log=print):
    urls = [u for u in dict.fromkeys(u for u in urls if u)]
    out = {}
    done = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(probe_one, u): u for u in urls}
        for f in cf.as_completed(futs):
            u = futs[f]
            try:
                out[u] = f.result()
            except Exception as exc:
                out[u] = {"input_url": u, "ok": False, "dead": True,
                          "error": f"probe crashed: {exc}", "dso_hits": [],
                          "zips_on_page": [], "final_domain": "", "title": ""}
            done += 1
            if done % 25 == 0:
                log(f"    probed {done}/{len(urls)}")
    return out


def main():
    import csv
    src = ROOT / "data" / "merged.csv"
    with src.open() as fh:
        rows = list(csv.DictReader(fh))
    urls = [r["website"] for r in rows if r["website"]]
    print(f"probing {len(set(urls))} unique practice sites")
    res = probe_all(urls)
    OUT.write_text(json.dumps(res, indent=1))
    live = sum(1 for v in res.values() if v["ok"] and not v["parked"])
    blocked = sum(1 for v in res.values() if v.get("blocked"))
    nodns = sum(1 for v in res.values() if not v.get("dns_ok"))
    dso = sum(1 for v in res.values() if v["dso_hits"])
    print(f"readable: {live}/{len(res)}   behind a bot wall: {blocked}   "
          f"DNS does not resolve: {nodns}   DSO footer hits: {dso}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
