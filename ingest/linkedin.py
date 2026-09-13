"""Company logos from LinkedIn.

Firms' own sites block scrapers (ark-funds.com answers 403), Wikidata only
covers the well-documented ones, and a firm's site logo is usually a wide
wordmark that has to be letterboxed into a square tile. LinkedIn has a square
logo for essentially every fund, at a predictable URL, on a page that serves
its og:image to anyone.

    python3 ingest/linkedin.py "ARK Investment Management"
"""
import re, sys, urllib.parse, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
PAGE = "https://www.linkedin.com/company/{}/"
LOGO = re.compile(r'https://media\.licdn\.com/dms/image/[^"\']+company-logo[^"\']*')
TITLE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', re.I)
TITLE2 = re.compile(r"<title[^>]*>([^<]{0,120})", re.I)
LEGAL = {"llc", "lp", "inc", "ltd", "co", "corp", "company", "the", "limited",
         "llp", "plc", "sa", "nv", "ag", "gmbh", "l", "p", "holdings"}


def slugs(name):
    """Candidate LinkedIn vanity names, most specific first."""
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", name.lower())
            if t and t not in LEGAL]
    if not toks:
        return []
    out = ["-".join(toks), "-".join(toks[:2]), "".join(toks[:2]), toks[0]]
    if len(toks) > 1:
        out.append(f"{toks[0]}-{toks[1][:6]}")
    seen, keep = set(), []
    for s in out:
        if len(s) > 2 and s not in seen:
            seen.add(s)
            keep.append(s)
    return keep[:6]


def logo(name, verbose=False):
    """(bytes, source_url) for a company's square LinkedIn logo."""
    for slug in slugs(name):
        try:
            body = urllib.request.urlopen(
                urllib.request.Request(PAGE.format(urllib.parse.quote(slug)),
                                       headers=UA), timeout=15
            ).read(400_000).decode("utf-8", "replace")
        except Exception:
            continue
        # "ares" is ARES IT Services on LinkedIn, "corvex" is Corvex
        # Uniformes, "king-street" is a consumer brand. A slug that resolves is
        # not a slug that belongs to this firm, so check the page says so.
        t = TITLE.search(body) or TITLE2.search(body)
        page = (t.group(1) if t else "").lower()
        want = [w for w in re.split(r"[^a-z0-9]+", name.lower())
                if w and w not in LEGAL]
        if want and not all(w in re.sub(r"[^a-z0-9]+", " ", page) for w in want[:2]):
            continue
        m = LOGO.search(body)
        if not m:
            continue
        url = m.group(0).replace("&amp;", "&")
        try:
            raw = urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=20).read()
        except Exception:
            continue
        if verbose:
            print(f"    linkedin.com/company/{slug}", flush=True)
        return raw, url
    return None, None


if __name__ == "__main__":
    raw, url = logo(" ".join(sys.argv[1:]) or "ARK Investment Management", verbose=True)
    print(f"{len(raw)} bytes" if raw else "not found", (url or "")[:90])
