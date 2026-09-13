"""Headshots from firm bio pages, matched on the person's own URL.

The previous approach looked for the name in a team page's HTML and took the
nearest preceding <img>. On modern fund sites that finds nothing: point72.com's
leadership page is a JS-rendered list of links with three images on it, and the
only place "Steven A. Cohen" appears in the raw HTML is a Yoast SEO meta tag.

What those sites do have is one page per person, at a URL containing the
person's name — /leader/steven-a-cohen/ — whose og:image is that person's
headshot. Matching on the URL slug is both more reliable than proximity and
self-verifying: the name is in the address, which is exactly the rule of
"fetch the photo where it says their name".

    python3 ingest/bios.py --only cohen
"""
import json, os, re, subprocess, sys, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import faces

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}

TEAM_PATHS = ["team", "leadership", "our-team", "people", "our-people", "about/team",
              "about/leadership", "firm/leadership", "about-us/leadership",
              "management", "partners", "professionals", "who-we-are", "about"]
NAVLINK = re.compile(r'href=["\']([^"\']*(?:team|leadership|our-people|/people|'
                     r'management|partners|professionals|bios?)[^"\']*)["\']', re.I)
AHREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)
OGIMG = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I)
OGIMG2 = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I)
IMGSRC = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)
STOP = {"jr", "sr", "ii", "iii", "iv", "dr", "mr", "ms", "mrs"}


def get(url, timeout=15, limit=3_000_000):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout).read(limit)


def dom(url, budget=6000):
    """Render with headless Chrome; most fund sites build their roster in JS."""
    try:
        r = subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--disk-cache-size=1",
             "--disable-application-cache", "--disable-background-networking",
             f"--virtual-time-budget={budget}", "--dump-dom", url],
            capture_output=True, text=True, timeout=30)
        return r.stdout or ""
    except Exception:
        return ""


def tokens(name):
    t = [re.sub(r"[^a-z]", "", w.lower()) for w in name.split()]
    return [w for w in t if w and w not in STOP]


def slug_matches(slug, person):
    """Does this URL slug name this person?

    Surname must be present, and the given name must agree -- otherwise
    /leader/jennifer-cohen/ would answer for Steve Cohen. Prefixes count, so
    "steve" matches "steven"; initials in the slug are skipped.
    """
    parts = [p for p in re.split(r"[^a-z]+", slug.lower()) if len(p) > 1]
    if not parts:
        return False
    toks = tokens(person)
    if len(toks) < 2:
        return False
    first, last = toks[0], toks[-1]
    if last not in parts:
        return False
    return any(p == first or p.startswith(first) or first.startswith(p)
               for p in parts if len(p) >= 3)


def team_pages(domain):
    """Candidate roster pages: the site's own nav links first, then guesses."""
    base = "https://www." + domain
    seen, out = set(), []
    try:
        home = get(base).decode("utf-8", "replace")
    except Exception:
        try:
            base = "https://" + domain
            home = get(base).decode("utf-8", "replace")
        except Exception:
            home = ""
    for href in NAVLINK.findall(home)[:25]:
        u = urllib.parse.urljoin(base + "/", href)
        if domain in u and u not in seen:
            seen.add(u)
            out.append(u)
    for p in TEAM_PATHS:
        u = f"{base}/{p}"
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:10]


def _links_in(body, page, domain):
    """Person-page links: /leader/steven-a-cohen/, /team/jane-doe, and so on."""
    found = {}
    for href in AHREF.findall(body):
        u = urllib.parse.urljoin(page, href.split("#")[0])
        if domain not in u:
            continue
        path = urllib.parse.urlparse(u).path.rstrip("/")
        tail = path.rsplit("/", 1)[-1]
        if (path.count("/") >= 2 and tail.count("-") >= 1
                and re.fullmatch(r"[a-z][a-z-]{4,60}", tail)):
            found[u] = tail
    return found


def roster(domain, want=2, budget=6):
    """Every person-page link on a firm's site, crawled once per firm.

    Cheap fetches first, across every candidate page; headless Chrome only if
    none of them named anybody. Rendering every page of every firm up front was
    minutes per firm and most sites never needed it.
    """
    pages = team_pages(domain)[:budget]
    links = {}
    for page in pages:
        try:
            links.update(_links_in(get(page).decode("utf-8", "replace"), page, domain))
        except Exception:
            continue
        if len(links) >= want:
            return links
    for page in pages[:2]:               # JS-built rosters, e.g. point72.com
        body = dom(page)
        if body:
            links.update(_links_in(body, page, domain))
        if len(links) >= want:
            break
    return links


def match(person, links):
    """The one link on a firm's site that names this person."""
    hits = [u for u, tail in links.items() if slug_matches(tail, person)]
    return sorted(hits, key=len)[0] if hits else None


def photo_on(url):
    """The portrait on a bio page: og:image first, then the largest <img>."""
    try:
        body = get(url).decode("utf-8", "replace")
    except Exception:
        return []
    out = []
    for rx in (OGIMG, OGIMG2):
        m = rx.search(body)
        if m:
            out.append(urllib.parse.urljoin(url, m.group(1)))
    for src in IMGSRC.findall(body)[:14]:
        out.append(urllib.parse.urljoin(url, src))
    seen, uniq = set(), []
    for u in out:
        if u not in seen and re.search(r"\.(jpe?g|png|webp)(\?|$)", u, re.I):
            seen.add(u)
            uniq.append(u)
    return uniq


def photo_by_filename(person, domain, pages=None):
    """Some rosters put the photo straight on the page, named after the person.

    /assets/team/john-smith.jpg is as strong a claim about who is pictured as a
    bio URL is, and it costs nothing extra: the roster pages are already fetched.
    """
    for page in (pages or team_pages(domain))[:4]:
        for body in (None, "r"):
            try:
                html = get(page).decode("utf-8", "replace") if body is None else dom(page)
            except Exception:
                continue
            if not html:
                continue
            for src in IMGSRC.findall(html):
                u = urllib.parse.urljoin(page, src)
                leaf = urllib.parse.urlparse(u).path.rsplit("/", 1)[-1]
                stem = re.sub(r"\.[a-z0-9]+$", "", leaf, flags=re.I)
                if slug_matches(stem, person):
                    return u
            if body is None and len(IMGSRC.findall(html)) > 8:
                break          # a page with real images does not need rendering
    return None


def find(person, domain, links=None):
    """(photo_local, source, why) for one principal."""
    slug = re.sub(r"[^a-z0-9]+", "-", person.lower()).strip("-")
    u = match(person, links if links is not None else roster(domain))
    why = "no bio page"
    if u:
        why = "no image on bio page"
        for img in photo_on(u):
            local, w = faces.process(img, slug)
            if local:
                return local, u, "ok"
            why = w
    direct = photo_by_filename(person, domain)
    if direct:
        local, w = faces.process(direct, slug)
        if local:
            return local, direct, "ok"
        why = w
    return None, None, why
