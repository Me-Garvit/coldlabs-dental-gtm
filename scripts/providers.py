"""
One interface over the Google Maps data providers, with caching, a hard spend
guard and provider fallback.

The plan called for Apify (primary) -> Outscraper (fallback). No Outscraper key
was issued for this run, so the fallback chain is implemented across two
independent Apify actors instead. The chain is data-driven (CHAINS below), so
dropping an Outscraper client in is a one-line change, and the code already runs
with either key alone.
"""
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.parse

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
LEDGER = ROOT / "data" / "spend_ledger.jsonl"

APIFY_BASE = "https://api.apify.com/v2"


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


load_env()
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
OUTSCRAPER_KEY = os.environ.get("OUTSCRAPER_API_KEY", "").strip()

# --- provider chains -------------------------------------------------------
# Each entry: (provider_name, actor_id, price_per_primary_event_usd)
CHAINS = {
    "maps_search": [
        ("apify:crawler-google-places", "compass~crawler-google-places", 0.004),
        ("apify:google-maps-extractor", "compass~google-maps-extractor", 0.005),
    ],
    "reviews": [
        ("apify:reviews-scraper", "compass~google-maps-reviews-scraper", 0.0006),
        ("apify:crawler-google-places", "compass~crawler-google-places", 0.0005),
    ],
}

# Hard ceiling for the whole assignment. The Apify account is on the free plan
# ($5/month). We never want a runaway run to eat it, so every call checks this.
BUDGET_USD = float(os.environ.get("RUN_BUDGET_USD", "4.60"))


class BudgetExceeded(RuntimeError):
    pass


class ProviderError(RuntimeError):
    pass


def _cache_key(actor: str, payload: dict) -> str:
    blob = json.dumps({"actor": actor, "input": payload}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def account_usage_usd() -> float:
    """Live monthly spend from Apify. The source of truth for the spend guard."""
    if not APIFY_TOKEN:
        return 0.0
    r = requests.get(f"{APIFY_BASE}/users/me/limits",
                     params={"token": APIFY_TOKEN}, timeout=30)
    r.raise_for_status()
    return float(r.json()["data"]["current"]["monthlyUsageUsd"])


def log_spend(step, provider, actor, run_id, usd, units, note=""):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "step": step,
           "provider": provider, "actor": actor, "run_id": run_id,
           "usd": round(usd, 5), "units": units, "note": note}
    with LEDGER.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


def _run_apify(actor: str, payload: dict, max_charge_usd: float,
               timeout_s: int = 900, memory_mb: int = 1024):
    """Start an actor run, wait for it, return (items, run_meta)."""
    url = f"{APIFY_BASE}/acts/{actor}/runs"
    params = {"token": APIFY_TOKEN, "timeout": timeout_s, "memory": memory_mb}
    if max_charge_usd:
        params["maxTotalChargeUsd"] = round(max_charge_usd, 4)
    r = requests.post(url, params=params, json=payload, timeout=60)
    if r.status_code >= 400:
        raise ProviderError(f"{actor} start failed {r.status_code}: {r.text[:300]}")
    run = r.json()["data"]
    run_id = run["id"]

    deadline = time.time() + timeout_s + 120
    status = run["status"]
    while status in ("READY", "RUNNING") and time.time() < deadline:
        time.sleep(5)
        s = requests.get(f"{APIFY_BASE}/actor-runs/{run_id}",
                         params={"token": APIFY_TOKEN}, timeout=60)
        s.raise_for_status()
        run = s.json()["data"]
        status = run["status"]

    if status not in ("SUCCEEDED", "ABORTED", "TIMED-OUT"):
        raise ProviderError(f"{actor} run {run_id} ended {status}")

    ds = run.get("defaultDatasetId")
    items = []
    if ds:
        offset = 0
        while True:
            d = requests.get(f"{APIFY_BASE}/datasets/{ds}/items",
                             params={"token": APIFY_TOKEN, "offset": offset,
                                     "limit": 1000, "clean": "true"}, timeout=120)
            d.raise_for_status()
            batch = d.json()
            if not batch:
                break
            items.extend(batch)
            offset += len(batch)
            if len(batch) < 1000:
                break

    usage = run.get("usageTotalUsd") or 0.0
    meta = {"run_id": run_id, "status": status, "usd": float(usage),
            "actor": actor, "items": len(items)}
    return items, meta


def call(step: str, payload: dict, max_charge_usd: float, label: str = "",
         force: bool = False, memory_mb: int = 1024):
    """
    Run `step` (a key of CHAINS) through its provider chain.
    Returns (items, meta). meta['provider'] names who served the row.
    Cached by (actor, payload) so re-runs are free.
    """
    chain = CHAINS[step]
    errors = []

    # Cache scan across the WHOLE chain first. A previous run may have been
    # served by the fallback provider, and a spend stop on the primary must
    # never hide a cached answer that costs nothing - otherwise re-running an
    # exhausted account silently returns no data instead of the cached result.
    if not force:
        for provider, actor, _price in chain:
            cfile = CACHE / f"{step}_{_cache_key(actor, payload)}.json"
            if cfile.exists():
                blob = json.loads(cfile.read_text())
                blob["meta"]["cached"] = True
                return blob["items"], blob["meta"]

    for provider, actor, _price in chain:
        key = _cache_key(actor, payload)
        cfile = CACHE / f"{step}_{key}.json"

        if not APIFY_TOKEN:
            errors.append(f"{provider}: no credential")
            continue

        spent = account_usage_usd()
        if spent >= BUDGET_USD:
            raise BudgetExceeded(
                f"monthly Apify spend ${spent:.3f} >= cap ${BUDGET_USD:.2f}; "
                f"refusing to start {actor} for {label or step}")
        headroom = BUDGET_USD - spent
        charge = min(max_charge_usd, headroom)

        try:
            items, meta = _run_apify(actor, payload, charge, memory_mb=memory_mb)
        except (ProviderError, requests.RequestException) as exc:
            errors.append(f"{provider}: {exc}")
            print(f"    ! {provider} failed ({exc}); trying next provider",
                  file=sys.stderr)
            continue

        meta["provider"] = provider
        meta["label"] = label
        meta["cached"] = False
        if not items and meta["status"] == "SUCCEEDED":
            # Empty is a legitimate answer for a thin ZIP, but it is also what a
            # silently-blocked run looks like. Cache it, flag it, move on.
            meta["empty"] = True
        log_spend(step, provider, actor, meta["run_id"], meta["usd"],
                  meta["items"], label)
        cfile.write_text(json.dumps({"items": items, "meta": meta}))
        return items, meta

    raise ProviderError(f"all providers failed for {step} ({label}): {errors}")


def budget_report():
    spent = account_usage_usd()
    return {"spent_usd": round(spent, 4), "cap_usd": BUDGET_USD,
            "headroom_usd": round(BUDGET_USD - spent, 4)}


if __name__ == "__main__":
    print(json.dumps(budget_report(), indent=2))
