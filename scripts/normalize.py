"""Shared normalization: phone, address, name match keys, domains."""
import json
import pathlib
import re

import tldextract

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "scripts/config/categories.json").read_text())

_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())

STREET_SUFFIXES = {
    "st", "ave", "blvd", "rd", "dr", "ln", "ct", "pl", "pkwy", "hwy", "fwy",
    "cir", "ter", "trl", "sq", "expy", "way", "loop", "run", "pt", "point",
    "plz", "row", "walk", "bend", "crossing", "xing",
}

UNIT_WORDS = {"ste", "suite", "unit", "apt", "bldg", "building", "fl", "floor",
              "rm", "room", "no"}

SUFFIX_MAP = {
    "street": "st", "str": "st", "avenue": "ave", "av": "ave", "boulevard": "blvd",
    "road": "rd", "drive": "dr", "lane": "ln", "court": "ct", "place": "pl",
    "parkway": "pkwy", "highway": "hwy", "freeway": "fwy", "circle": "cir",
    "terrace": "ter", "trail": "trl", "square": "sq", "expressway": "expy",
    "suite": "ste", "building": "bldg", "floor": "fl", "north": "n",
    "south": "s", "east": "e", "west": "w", "northeast": "ne",
    "northwest": "nw", "southeast": "se", "southwest": "sw",
    # both sources abbreviate these inconsistently
    "point": "pt", "heights": "hts", "junction": "jct", "extension": "ext",
    "turnpike": "tpke", "crossing": "xing", "plaza": "plz", "center": "ctr",
    "centre": "ctr", "mount": "mt", "fort": "ft", "saint": "st",
}

UNIT_RE = re.compile(
    r"(?:\b(?:ste|suite|unit|apt|bldg|building|fl|floor|rm|room)\b\.?|#)\s*([\w-]+)", re.I)

LEGAL_SUFFIXES = [
    "llc", "l.l.c.", "pllc", "p.l.l.c.", "plc", "pc", "p.c.", "pa", "p.a.",
    "inc", "inc.", "incorporated", "corp", "corporation", "ltd", "llp", "lp",
    "co", "company", "dds", "dmd", "d.d.s.", "d.m.d.", "md", "d.m.d",
]


def norm_phone(value):
    d = re.sub(r"\D", "", str(value or ""))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return "+1" + d if len(d) == 10 else ""


def split_unit(street):
    """Return (street_without_unit, unit)."""
    s = (street or "").strip()
    m = UNIT_RE.search(s)
    unit = m.group(1).lower() if m else ""
    if m:
        s = (s[:m.start()] + " " + s[m.end():]).strip()
    s = re.sub(r"[#,]+\s*$", "", s).strip()
    return s, unit


def norm_street(street):
    """
    Normalised street with the unit stripped, however it was written.

    Two defects this guards against, both seen in real data:
      "3201 Allen Pkwy Ste #E200" -> split_unit takes "#E200" and leaves the
        word "Ste" orphaned at the end.
      "1400 Blalock Rd D1"        -> a bare unit token with no marker word at
        all, where the other source writes "Ste D1".
    Both made the same address fail to match itself across sources.
    """
    s, _ = split_unit(street)
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    toks = [SUFFIX_MAP.get(t, t) for t in s.split()]

    # drop orphaned unit marker words left at the end ("... pkwy ste")
    while len(toks) > 2 and toks[-1] in UNIT_WORDS:
        toks.pop()

    # drop a bare trailing unit token that follows a street suffix:
    # "blalock rd d1" -> "blalock rd", but never "lookout point" -> "lookout"
    if (len(toks) > 2 and toks[-2] in STREET_SUFFIXES
            and re.fullmatch(r"[a-z]?\d+[a-z]?|[a-z]", toks[-1])):
        toks.pop()

    while len(toks) > 2 and toks[-1] in UNIT_WORDS:
        toks.pop()
    return " ".join(toks).strip()


def street_number(street):
    m = re.match(r"\s*(\d+)", street or "")
    return m.group(1) if m else ""


def norm_zip(z):
    d = re.sub(r"\D", "", str(z or ""))
    return d[:5] if len(d) >= 5 else ""


def name_key(name):
    """Aggressive key used only for matching, never for display."""
    n = (name or "").lower()
    n = re.sub(r"[^\w\s]", " ", n)
    toks = [t for t in n.split() if t not in
            {s.replace(".", "") for s in LEGAL_SUFFIXES}]
    toks = [t for t in toks if t not in
            {"the", "of", "and", "at", "a", "dr", "doctor"}]
    return " ".join(sorted(set(toks)))


def name_seq(name):
    n = (name or "").lower()
    n = re.sub(r"[^\w\s]", " ", n)
    toks = [t for t in n.split() if t not in
            {s.replace(".", "") for s in LEGAL_SUFFIXES}]
    return " ".join(toks)


def root_domain(url):
    """Registered root domain, lowercase, no scheme/www/path. '' if unusable."""
    if not url:
        return ""
    u = str(url).strip()
    if not re.match(r"^https?://", u, re.I):
        u = "http://" + u
    try:
        ext = _EXTRACT(u)
    except Exception:
        return ""
    if not ext.domain or not ext.suffix:
        return ""
    host = f"{ext.domain}.{ext.suffix}".lower()
    sub = (ext.subdomain or "").lower()
    # site builders have no useful root domain - keep the full host
    if host in CFG["builder_hosts"] or any(host.endswith(b) for b in CFG["builder_hosts"]):
        sub = re.sub(r"^www\.", "", sub)
        return f"{sub}.{host}" if sub else host
    return host


def is_directory_domain(domain):
    if not domain:
        return False
    return any(domain == h or domain.endswith("." + h)
               for h in CFG["non_website_hosts"])


def is_builder_domain(domain):
    return any(domain.endswith(b) for b in CFG["builder_hosts"])


def addr_key(street, zipcode):
    n, z = street_number(street), norm_zip(zipcode)
    return f"{n}|{z}" if n and z else ""


def full_addr_key(street, zipcode):
    s, z = norm_street(street), norm_zip(zipcode)
    return f"{s}|{z}" if s and z else ""
