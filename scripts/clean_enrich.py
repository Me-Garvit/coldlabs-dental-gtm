"""
Stage 4 - clean and enrich.

Company name is cleaned in two steps. Step 1 is deterministic rules, which
handle the large majority. Step 2 flags the leftovers for a Claude pass
(scripts/name_review.py writes the queue; the reviewed names come back in
data/name_overrides.json). Guardrails on the Claude step: the output may only
use words present in the original, max 6 words, never invent.

Domain: follow redirects -> registered root via tldextract -> lowercase, no
scheme/www/path/query. Site-builder hosts keep their subdomain and are flagged
`builder_domain`, because there is no root domain to email.
"""
import argparse
import csv
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import normalize as nz

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "qualified.csv"
PROBES = ROOT / "data" / "site_probes.json"
OVERRIDES = ROOT / "data" / "name_overrides.json"
LINKEDIN = ROOT / "data" / "linkedin.json"
OUT = ROOT / "data" / "enriched.csv"
QUEUE = ROOT / "data" / "name_review_queue.json"

EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⬀-⯿]")

LEGAL = re.compile(
    r"[\s,]*\b(l\.?l\.?c|p\.?l\.?l\.?c|p\.?l\.?c|p\.?c|p\.?a|inc(?:orporated)?|corp(?:oration)?|ltd|l\.?l\.?p|co)\b\.?",
    re.I)

TAIL_MARKERS = re.compile(
    r"(best|top[- ]rated|#1|number one|affordable|cheap|near me|"
    r"dentist in|dentistry in|dental in|serving|call |book |"
    r"\d{3}[-.\s]?\d{3}[-.\s]?\d{4}|"
    r"\b(tx|fl|texas|florida|houston|dallas|tampa|orlando)\b)", re.I)

SPLITTERS = [" | ", " – ", " — ", " - ", ": ", " :: ", " • "]

CRED = re.compile(r"\b(d\.?d\.?s|d\.?m\.?d|m\.?d|d\.?o|f\.?a\.?g\.?d|f\.?i\.?c\.?o\.?i)\b\.?", re.I)
PERSON = re.compile(r"^(?:[Dd][Rr]\.?\s+)?([A-Z][a-z'’-]+)\s+(?:[A-Z]\.?\s+)?([A-Z][a-z'’-]+)$")

# A head that is nothing but an ad. Only these let the tail win; a city name in
# the head does not, because "Advanced Dentistry of Tampa Bay" is the real name.
AD_ONLY_HEAD = re.compile(
    r"^(best|top[- ]rated|#1|number one|affordable|cheap|your |the best)\b|"
    r"^\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}$", re.I)

ACRONYMS = {"TX", "FL", "NYC", "USA", "DDS", "DMD", "MD", "TMJ", "LA", "NW",
            "NE", "SW", "SE", "II", "III", "IV", "AC", "OC"}


SMALL_WORDS = {"of", "the", "and", "at", "for", "in", "on", "a", "an", "to", "by"}


def titlecase(s):
    out = []
    for i, w in enumerate(s.split()):
        if w.upper() in ACRONYMS:
            out.append(w.upper())
        elif i > 0 and w.lower() in SMALL_WORDS:
            out.append(w.lower())
        elif w.isupper() and len(w) > 1:
            out.append(w.capitalize())
        else:
            out.append(w)
    return " ".join(out)


def clean_name(raw):
    """Return (clean_name, uncertain_reason or '')."""
    n = EMOJI.sub("", raw or "").strip()
    n = re.sub(r"\s+", " ", n)
    original_words = set(re.findall(r"[a-z]+", n.lower()))

    # 1. cut marketing tails
    for sp in SPLITTERS:
        if sp in n:
            head, _, tail = n.partition(sp)
            head, tail = head.strip(), tail.strip()
            if AD_ONLY_HEAD.match(head) and tail:
                n = tail          # the head was pure advertising
            elif head:
                n = head          # default: the name is what comes first
    n = re.sub(r"\s*\(\s*(formerly|fka|dba)[^)]*\)", "", n, flags=re.I)
    # a trailing parenthetical is a descriptor, not part of the name:
    # "Hermann Park Smiles (Family & Cosmetic Dentistry)" -> "Hermann Park Smiles"
    stripped_paren = re.sub(r"\s*\([^)]*\)\s*$", "", n).strip()
    if len(stripped_paren.split()) >= 2:
        n = stripped_paren

    # 2. legal suffixes - but never strip so much that the brand disappears.
    # "Smile Co." is a name; "Smile Co. LLC" is a name plus a suffix.
    de_legal = LEGAL.sub("", n).strip(" ,-|")
    de_legal = re.sub(r"\s*,\s*$", "", de_legal)
    if len(de_legal.split()) >= 2 or len(n.split()) < 2:
        n = de_legal

    # 3. caps / spacing
    if n and (n.isupper() or sum(c.isupper() for c in n) > 0.75 * max(1, sum(c.isalpha() for c in n))):
        n = titlecase(n.title())
    n = re.sub(r"\s+", " ", n).strip(" ,-|&")

    # 4. "John Smith DDS" -> "Dr. John Smith"
    stripped = CRED.sub("", n).strip(" ,.-")
    m = PERSON.match(stripped)
    if m and CRED.search(n) and not re.search(r"dent|smile|ortho|family|care", n, re.I):
        n = f"Dr. {m.group(1)} {m.group(2)}"
    elif CRED.search(n) and re.search(r"dent|smile|ortho|family|care", n, re.I):
        n = CRED.sub("", n).strip(" ,.-")
        n = re.sub(r"\s+", " ", n)

    n = n.strip(" ,-|&.")

    # 5. flag the uncertain ones for review
    reason = ""
    if not n:
        reason = "empty after cleaning"
    elif len(n.split()) > 6:
        reason = f"{len(n.split())} words"
    elif TAIL_MARKERS.search(n):
        reason = "marketing/geo words survived"
    elif re.search(r"[|/]|  ", n):
        reason = "separator survived"
    elif len(n) < 3:
        reason = "too short"
    # "Dr." is a title we are allowed to add; everything else must come from
    # the original string.
    new_words = set(re.findall(r"[a-z]+", n.lower())) - {"dr"}
    if n and not new_words <= original_words:
        reason = "invented words"
    return (n or raw).strip(), reason


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    with open(args.src) as fh:
        rows = list(csv.DictReader(fh))
    probes = json.loads(PROBES.read_text()) if PROBES.exists() else {}
    overrides = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    linkedin = json.loads(LINKEDIN.read_text()) if LINKEDIN.exists() else {}

    queue = []
    for r in rows:
        clean, reason = clean_name(r["name"])
        if r["lead_id"] in overrides:
            clean, reason = overrides[r["lead_id"]], ""
        r["company_name"] = clean
        r["name_raw"] = r["name"]
        r["name_flag"] = reason
        if reason:
            queue.append({"lead_id": r["lead_id"], "raw": r["name"],
                          "rule_output": clean, "reason": reason})

        probe = probes.get(r["website"]) or {}
        dom = probe.get("final_domain") or nz.root_domain(r["website"])
        r["domain"] = dom
        r["builder_domain"] = "yes" if nz.is_builder_domain(dom) else ""
        r["website_final_url"] = probe.get("final_url", "")
        r["linkedin_url"] = linkedin.get(r["lead_id"], "")
        r["zip"] = nz.norm_zip(r["zip"])

    fields = list(rows[0].keys())
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    QUEUE.write_text(json.dumps(queue, indent=2))
    print(f"cleaned {len(rows)} names; {len(queue)} flagged for Claude review "
          f"-> {QUEUE}")
    print(f"LinkedIn filled: {sum(1 for r in rows if r['linkedin_url'])}/{len(rows)}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
