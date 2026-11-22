"""
Page fetcher with two tiers, and a clear rule about what we do NOT do.

Tier 1 - plain HTTP. This is what WebMD actually serves today: a normal
User-Agent gets the full server-rendered provider cards, so starting a browser
for every page would be pure waste.

Tier 2 - Camoufox (github.com/daijro/camoufox), an anti-detect Firefox build
driven through Playwright. We use it as the browser-automation engine the plan
called for, for two honest jobs:
  * rendering pages that need JavaScript to produce their content (WebMD is a
    Vue app that currently server-renders, but a template change would move it
    client-side, and small practice sites on Wix/Squarespace render client-side
    today);
  * holding a real browser session across a paginated crawl.

What tier 2 is NOT used for: getting past a site that is actively refusing us.
If a host answers 403/429 or puts up a CAPTCHA or interstitial, that is the
site declining automated access, and we stop and log it rather than re-dress
the request to look like someone else. `escalate_on_block` is therefore False
by default, and `fetch()` raises Blocked instead of retrying in a browser.
That is also what the build plan specified for WebMD: a hard stop on a CAPTCHA
wall, blocks logged and never silently skipped.
"""
import random
import re
import threading
import time

import requests

UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0",
]

# A host saying "no". We never try to dress around these.
REFUSAL_STATUSES = (401, 403, 429, 451)
REFUSAL_MARKERS = (
    "pardon our interruption", "access denied", "are you a human",
    "captcha", "incapsula", "cf-browser-verification", "attention required",
    "request unsuccessful", "bot detection", "unusual traffic",
    "verify you are human", "enable javascript and cookies to continue",
)

_LOCK = threading.Lock()
_BROWSER = None
_CAMO_CTX = None


class Blocked(RuntimeError):
    """The host declined automated access. Terminal - do not route around it."""


class ThinPage(RuntimeError):
    """Served 200 but no usable content - usually needs JavaScript."""


def refusal_reason(status, html):
    """Return a string if the host is refusing us, else ''."""
    if status in REFUSAL_STATUSES:
        return f"HTTP {status}"
    low = (html or "")[:60000].lower()
    for m in REFUSAL_MARKERS:
        if m in low:
            return f"interstitial: {m!r}"
    return ""


def _session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UAS[0],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
        "Connection": "keep-alive",
    })
    return s


SESSION = _session()


def _browser():
    """One Camoufox browser per process, reused across pages."""
    global _BROWSER, _CAMO_CTX
    with _LOCK:
        if _BROWSER is None:
            from camoufox.sync_api import Camoufox
            _CAMO_CTX = Camoufox(headless=True, geoip=False)
            _BROWSER = _CAMO_CTX.__enter__()
        return _BROWSER


def close_browser():
    global _BROWSER, _CAMO_CTX
    with _LOCK:
        if _CAMO_CTX is not None:
            try:
                _CAMO_CTX.__exit__(None, None, None)
            except Exception:
                pass
        _BROWSER, _CAMO_CTX = None, None


def render(url, *, wait_selector=None, timeout_ms=45000, log=print):
    """Tier 2 on purpose: load `url` in a real browser and return its HTML."""
    browser = _browser()
    page = browser.new_page()
    try:
        resp = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        status = resp.status if resp else 0
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=15000)
            except Exception:
                pass
        page.wait_for_timeout(random.randint(800, 1800))
        html = page.content()
        why = refusal_reason(status, html)
        if why:
            raise Blocked(f"{url} declined automated access ({why})")
        return html, "camoufox", status
    finally:
        page.close()


def fetch(url, *, timeout=30, retries=1, min_bytes=2000, wait_selector=None,
          needs_js=False, escalate_on_block=False, log=print):
    """
    Return (html, tier, status).

    needs_js=True  -> go straight to the browser (the page's content is
                      client-rendered, so HTTP would return an empty shell).
    Blocked        -> the host refused. Not retried in a browser unless the
                      caller explicitly opts in via escalate_on_block, which
                      exists only for sites we have standing to automate.
    """
    if needs_js:
        return render(url, wait_selector=wait_selector, log=log)

    last = None
    for attempt in range(retries + 1):
        try:
            r = SESSION.get(url, timeout=timeout, allow_redirects=True)
            last = r.status_code
            why = refusal_reason(r.status_code, r.text)
            if why:
                if escalate_on_block:
                    log(f"    {url}: {why}; caller opted into browser retry")
                    return render(url, wait_selector=wait_selector, log=log)
                raise Blocked(f"{url} declined automated access ({why})")
            if r.status_code >= 500:
                log(f"    {url}: HTTP {r.status_code}, retrying")
            elif len(r.text or "") < min_bytes:
                # 200 but no content: a JS shell, not a refusal. A browser is
                # the right answer here and nothing is being circumvented.
                log(f"    {url}: thin response ({len(r.text or '')}B), rendering")
                return render(url, wait_selector=wait_selector, log=log)
            else:
                return r.text, "http", r.status_code
        except Blocked:
            raise
        except requests.RequestException as exc:
            log(f"    {url}: {exc}")
        if attempt < retries:
            time.sleep(2 ** attempt * random.uniform(1.5, 3.0))

    raise ThinPage(f"{url} unreachable over HTTP (last status {last})")


def polite_sleep(lo=2.0, hi=5.0):
    time.sleep(random.uniform(lo, hi))
