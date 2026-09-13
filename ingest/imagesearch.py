"""Headshots via Bing image search, gated on the caption naming the person.

Most fund principals have no Wikipedia photo and work at firms that publish no
team page, so the only remaining source is open search. That is also how the
wrong faces got in last time -- an untargeted lookup returned a rally driver
for Dan Loeb and an actor for Nelson Peltz -- so nothing here is accepted on
the strength of the query alone.

Every candidate must clear four gates:

  adjacency  the caption must contain the given and family name *next to each
             other*, so "Sebastien Loeb and Daniel Elena" cannot answer for
             Daniel Loeb the way a bag-of-words match would
  context    the caption or source page must mention the firm or read as
             finance, which separates same-name people in other fields
  face       faces.py must find an actual centred face, not a logo or a chart
  identity   one image is used for exactly one person

Google blocks scripted image search (302); Bing answers, so Bing it is.
"""
import html, io, json, os, re, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import faces

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
REJECTS = os.path.join(DATA, "photo_rejects.json")


def rejected():
    """URLs a human looked at and said no to.

    No statistic reliably separates a caricature from a photograph - a cartoon
    of Alex Sacerdote scored the same as Bill Ackman's headshot on every
    measure tried - so the judgement stays human and the answer is remembered.
    """
    try:
        return set(json.load(open(REJECTS)))
    except Exception:
        return set()


def reject(urls):
    cur = rejected() | set(u for u in urls if u)
    json.dump(sorted(cur), open(REJECTS, "w"), indent=1)
    return len(cur)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
ENDPOINT = "https://www.bing.com/images/search?q={}&form=HDRSC2&first=1"
IUSC = re.compile(r'class="iusc"[^>]*\sm="([^"]+)"')

FINANCE = re.compile(
    r"(hedge|fund|capital|invest|asset|portfolio|equity|manager|partner|"
    r"trader|trading|billionaire|wall street|cio|ceo|founder|chairman|"
    r"markets?|stocks?|13f|activist|financ|firm|llc|lp\b)", re.I)
# Photo pages that serve a placeholder, a crawler stub, or an expiring token.
BADHOST = re.compile(r"(lookaside\.fbsbx|/crawler/|gravatar|placeholder|"
                     r"logo|sprite|icon|avatar-default)", re.I)
# Served straight from a stock agency means a comp print with the agency's
# watermark burned across the face. News sites license clean copies of the same
# photograph, so reject the agency's own host, not the word "getty" in a name.
WATERMARK = re.compile(r"(^|\.)(gettyimages\.[a-z.]+|alamy\.com|shutterstock\.com|"
                       r"dreamstime\.com|123rf\.com|imago-images\.[a-z]+|"
                       r"zumapress\.com|agefotostock\.com|profimedia\.[a-z]+)$", re.I)
# This CDN is Insider Monkey's, and every portrait on it is a drawn caricature:
# it is where both the Alex Sacerdote and Matthew Halbower cartoons came from.
CARTOON_HOST = re.compile(r"(d2gr5kl7dt2z3t\.cloudfront\.net|insidermonkey\.com)", re.I)


def usable_host(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    return not (WATERMARK.search(host) or CARTOON_HOST.search(url))
NICK = {"dan": "daniel", "bill": "william", "bob": "robert", "steve": "steven",
        "mike": "michael", "jim": "james", "tom": "thomas", "dave": "david",
        "rick": "richard", "chris": "christopher", "nick": "nicholas",
        "tony": "anthony", "ted": "edward", "ken": "kenneth", "andy": "andrew",
        "joe": "joseph", "greg": "gregory", "matt": "matthew", "sam": "samuel",
        "ben": "benjamin", "alex": "alexander", "charlie": "charles"}


def get(url, timeout=15, limit=4_000_000):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout).read(limit)


def name_forms(person):
    """Given/family name pairs to accept, including the obvious nicknames."""
    toks = [re.sub(r"[^a-zA-Z]", "", t) for t in person.split()]
    toks = [t for t in toks if len(t) > 1]
    if len(toks) < 2:
        return []
    first, last = toks[0].lower(), toks[-1].lower()
    firsts = {first, NICK.get(first, first)}
    firsts |= {k for k, v in NICK.items() if v == first}
    return [(f, last) for f in firsts]


def names_adjacent(text, person):
    """Do the given and family name appear together, in either order?

    This is the gate that a bag-of-words match fails: it is what separates
    "Daniel Loeb" from "Sebastien Loeb and Daniel Elena".
    """
    t = text.lower()
    for first, last in name_forms(person):
        # first [middle initial] last  -- allow "Daniel S. Loeb"
        if re.search(rf"\b{first}\w*\.?\s+(?:[a-z]\.?\s+){{0,2}}{last}\b", t):
            return True
        if re.search(rf"\b{last},\s+{first}\w*\b", t):
            return True
    return False


def search(person, firm, extra="", pages=(0, 1)):
    """Bing image results as (image_url, caption, source_page)."""
    out = []
    for first in pages:
        q = urllib.parse.quote(f'"{person}" {firm} {extra}'.strip())
        url = ENDPOINT.format(q) + f"&first={first * 35 + 1}"
        try:
            body = get(url).decode("utf-8", "replace")
        except Exception:
            break
        for m in IUSC.findall(body):
            try:
                d = json.loads(html.unescape(m))
            except Exception:
                continue
            murl, t, purl = d.get("murl", ""), d.get("t", ""), d.get("purl", "")
            if murl and not BADHOST.search(murl) and usable_host(murl):
                out.append((murl, t, purl))
        if len(out) >= 20:
            break
        time.sleep(0.4)
    return out


def score(murl, title, purl, person, firm):
    """Rank candidates by how strongly they claim to *be* this person.

    A caption can name someone the picture merely discusses: Dan Loeb's first
    result was a CIK-numbered header graphic on an article about him. The
    image's own filename is the stronger claim, so it outranks the caption.
    """
    leaf = urllib.parse.unquote(murl.rsplit("/", 1)[-1].split("?")[0]).lower()
    n = 0
    if names_adjacent(leaf.replace("-", " ").replace("_", " "), person):
        n += 4
    if names_adjacent(title, person):
        n += 2
    if title.lower().startswith(person.split()[0].lower()):
        n += 1
    if firm and firm.split()[0].lower() in f"{title} {purl}".lower():
        n += 1
    if re.search(r"(getty|reuters|bloomberg|cnbc|forbes|wsj|ft\.com|nypost|"
                 r"businessinsider|nytimes|barrons|fortune)", purl, re.I):
        n += 2
    return n


def find(person, firm, slug, used=frozenset(), verbose=False):
    """(photo_local, source_url, why) -- or (None, None, reason)."""
    why = "no search results"
    used = set(used) | rejected()
    # "Cathie Wood" ARK Investment Management LLC returns two junk images; the
    # legal suffix narrows the query to nothing. Try the short firm name first,
    # then the bare one. Earlier this stopped after the first query that
    # returned *any* result, even when every one of them failed the gates.
    short = " ".join((firm or "").replace(",", " ").split()[:2])
    variants = [v for v in (short, firm, "hedge fund investor") if v]
    seen = []
    for ctx in dict.fromkeys(variants):
        try:
            hits = search(person, ctx)
        except Exception as e:
            return None, None, type(e).__name__
        ranked = sorted(hits, key=lambda h: -score(h[0], h[1], h[2], person, firm))
        for murl, title, purl in ranked:
            if murl in used or murl in seen:
                continue
            seen.append(murl)
            blob = f"{title} {urllib.parse.unquote(purl)} {urllib.parse.unquote(murl)}"
            leaf = urllib.parse.unquote(murl.rsplit("/", 1)[-1]).replace("-", " ")
            if not (names_adjacent(blob, person) or names_adjacent(leaf, person)):
                why = "caption does not name them"
                continue
            named_in_caption = names_adjacent(blob, person)
            ctxok = (firm and firm.split()[0].lower() in blob.lower()) or FINANCE.search(blob)
            if not ctxok:
                why = "no finance context"
                continue
            if not named_in_caption and firm and \
                    firm.split()[0].lower() not in blob.lower():
                why = "filename only, firm not named"
                continue
            local, w = faces.process(murl, slug, min_frac=0.10, min_src=260,
                                     max_band=3.0)
            if local:
                return local, murl, "ok"
            why = w
    return None, None, why
