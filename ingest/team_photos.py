"""Headshots from a firm's own team page, matched by name proximity.

The previous crawler required the person's surname to appear in the image
filename, which is not how team pages are built — the photo is a separate
asset near the name in the markup. It found 9 of 143.

This does what a person does: locate the name in the page, then take the
nearest image before it. That is the layout of essentially every leadership
page — portrait above, name beneath.

If neither this nor a Wikipedia article yields a photo, the person gets no
photo. Guessing from image search is what produced a map and a framed drawing.
"""
import json, os, re, sys, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36"}
IMG = re.compile(r"<img\b[^>]*>", re.I)
SRC = re.compile(r"""\b(?:data-src|data-lazy-src|data-original|srcset|src)\s*=\s*["']([^"']+)["']""", re.I)
JUNK = re.compile(r"(logo|icon|sprite|placeholder|pixel|spacer|banner|bg-|background|"
                  r"favicon|arrow|chevron|search|menu|close)", re.I)
# Match the keyword in the href, not the link text — sites put it in the URL
# (/en-ca/investments/our-global-team/) and the visible text is often just an
# icon or a short label.
NAV = re.compile(r'href=["\']([^"\']*(?:team|leadership|our-people|/people|'
                 r'management|executive|partners|professionals|bios|about-us)'
                 r'[^"\']*)["\']', re.I)
PATHS = ["team", "leadership", "our-team", "people", "our-people", "about/leadership",
         "about-us/leadership", "about/team", "about/our-team", "firm/leadership",
         "who-we-are/leadership", "about", "our-firm/leadership", "leadership-team"]


def get(url, timeout=14, limit=2_000_000):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                  timeout=timeout).read(limit)


def pages_for(domain):
    """Guessed paths plus whatever the site's own navigation points at."""
    out = []
    for base in (f"https://www.{domain}", f"https://{domain}"):
        try:
            home = get(base).decode("utf-8", "replace")
        except Exception:
            continue
        # Links the site actually publishes beat paths we invented.
        for m in NAV.finditer(home):
            href = m.group(1)
            if re.search(r"\.(pdf|jpg|png|svg|zip|docx?)$", href, re.I):
                continue
            if href.startswith(("mailto:", "tel:", "#", "javascript:")):
                continue
            out.append(urllib.parse.urljoin(base + "/", href))
        out += [f"{base}/{p}" for p in PATHS]
        break
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq[:26]


def photo_near_name(html, page_url, person):
    """Find the person's name in the markup, then the closest image above it."""
    first, last = person.split()[0], person.split()[-1]
    # the name as rendered may sit across tags, so search on a flattened copy
    pat = re.compile(re.escape(first) + r"[^<]{0,40}?" + re.escape(last), re.I)
    hit = pat.search(re.sub(r"<[^>]+>", lambda m: " " * len(m.group(0)), html))
    if not hit:
        return None
    pos = hit.start()
    best = None
    for m in IMG.finditer(html):
        if m.end() > pos:
            break                     # images after the name belong to the next person
        best = m
    if not best:
        return None
    src = SRC.search(best.group(0))
    if not src:
        return None
    url = src.group(1).split()[0].split("?")[0]
    if JUNK.search(url):
        return None
    # too far above the name and it is a different card entirely
    if pos - best.end() > 2500:
        return None
    return urllib.parse.urljoin(page_url, url)


def find(person, domain, verbose=True):
    for page in pages_for(domain):
        try:
            html = get(page).decode("utf-8", "replace")
        except Exception:
            continue
        url = photo_near_name(html, page, person)
        if url:
            if verbose:
                print(f"  {person:26s} {url[:78]}")
            return {"photo": url, "source": f"{domain} team page"}
    return None


if __name__ == "__main__":
    from logos import DOMAINS
    people = json.load(open(os.path.join(DATA, "people.json")))
    todo = [(k, v) for k, v in people.items()
            if not v.get("photo") and DOMAINS.get(v.get("firm"))]
    print(f"{len(todo)} principals without a photo and with a known firm site\n")
    found = 0
    for person, p in todo:
        hit = find(person, DOMAINS[p["firm"]])
        if hit:
            p.update(hit)
            found += 1
            json.dump(people, open(os.path.join(DATA, "people.json"), "w"), indent=2)
    print(f"\n{found} headshots found on firm team pages")
