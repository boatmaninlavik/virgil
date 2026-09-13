"""Canonical brand logos via Wikidata P154 -> Wikimedia Commons.

Scraping a firm's own site gets whatever that site happens to ship, which for
Third Point was an unmodified "Apple Touch Icon" placeholder and for TPG was a
podcast cover. Wikidata's P154 ("logo image") is a curated claim pointing at a
Commons file, and Commons rasterises SVG on demand — so one lookup yields the
real wordmark at whatever width we ask for, already transparent.

Free, keyless, no rate limit beyond ordinary courtesy.
"""
import json, re, time, urllib.parse, urllib.request

UA = {"User-Agent": "Virgil/1.0 (personal research tool; sean@erised.me)"}
API = "https://www.wikidata.org/w/api.php"
FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/"

# Wikidata search happily returns a person for a fund name, or a football club
# for a three-letter ticker. Require the description to read like a company.
FIRMISH = re.compile(
    r"(company|corporation|firm|bank|fund|investment|asset|manage|capital|"
    r"holding|group|financial|enterprise|business|conglomerate|insurer|"
    r"brokerage|partnership|multinational|organization|organisation)", re.I)
PERSONISH = re.compile(r"(politician|actor|footballer|singer|writer|player|"
                       r"musician|artist|athlete|born \d{4})", re.I)
_last = [0.0]


def _get(url, timeout=20):
    gap = time.time() - _last[0]
    if gap < 0.25:
        time.sleep(0.25 - gap)          # courtesy; the API has no hard limit
    _last[0] = time.time()
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout).read()


def _search(name, limit=5):
    u = (f"{API}?action=wbsearchentities&format=json&language=en"
         f"&limit={limit}&type=item&search={urllib.parse.quote(name)}")
    try:
        return json.loads(_get(u)).get("search", [])
    except Exception:
        return []


def _logo_file(qid):
    u = f"{API}?action=wbgetclaims&format=json&property=P154&entity={qid}"
    try:
        claims = json.loads(_get(u)).get("claims", {}).get("P154", [])
    except Exception:
        return None
    for c in claims:
        try:
            return c["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
    return None


def website(name, extra_terms=()):
    """Official website (P856) for a company, for logo scraping."""
    for term in (name,) + tuple(extra_terms):
        for hit in _search(term):
            desc = hit.get("description", "")
            if PERSONISH.search(desc) or not FIRMISH.search(desc):
                continue
            u = f"{API}?action=wbgetclaims&format=json&property=P856&entity={hit['id']}"
            try:
                claims = json.loads(_get(u)).get("claims", {}).get("P856", [])
            except Exception:
                continue
            for c in claims:
                try:
                    url = c["mainsnak"]["datavalue"]["value"]
                except (KeyError, TypeError):
                    continue
                host = urllib.parse.urlparse(url).netloc.lower()
                if host:
                    return host[4:] if host.startswith("www.") else host
    return None


WIKI_API = "https://en.wikipedia.org/w/api.php"
LOGO_FILE = re.compile(r"logo|wordmark|brandmark", re.I)
# Every Wikipedia article carries the project's own furniture, and
# "Commons-logo.svg" matches a logo search perfectly while being Wikimedia's
# icon rather than the company's.
WIKI_CHROME = re.compile(
    r"^(commons|wikidata|wikiquote|wikisource|wiktionary|wikinews|wikibooks|"
    r"wikiversity|wikivoyage|wikispecies|meta|mediawiki|wikimedia)[-_]?logo|"
    r"^(portal|folder|ambox|edit|question|symbol|padlock|increase|decrease|"
    r"red[ -]?x|green[ -]?check|office|crystal|nuvola|emblem|flag)", re.I)


def article_logo(name):
    """Logo from the English Wikipedia article's own image list.

    P154 is a curated claim and plenty of firms have none: ARK Invest has no
    P154 at all, yet its article carries File:Ark-logo-1-1.svg. Non-free logos
    live on en.wikipedia rather than Commons, so ask the article directly.
    """
    try:
        srch = json.loads(_get(
            f"{WIKI_API}?action=query&format=json&list=search&srlimit=3"
            f"&srsearch={urllib.parse.quote(name)}")).get("query", {}).get("search", [])
    except Exception:
        return None, None
    key = re.sub(r"[^a-z]", "", name.split()[0].lower())
    for hit in srch:
        title = hit["title"]
        if key and key not in re.sub(r"[^a-z]", "", title.lower()):
            continue
        try:
            pages = json.loads(_get(
                f"{WIKI_API}?action=query&format=json&redirects=1&prop=images"
                f"&imlimit=40&titles={urllib.parse.quote(title)}"))["query"]["pages"]
        except Exception:
            continue
        files = [i["title"][5:] for i in
                 list(pages.values())[0].get("images", [])
                 if LOGO_FILE.search(i["title"])
                 and not WIKI_CHROME.search(i["title"][5:])]
        for fn in files:
            url = ("https://en.wikipedia.org/wiki/Special:FilePath/"
                   + urllib.parse.quote(fn.replace(" ", "_")) + "?width=512")
            try:
                return _get(url, timeout=25), url
            except Exception:
                continue
    return None, None


def logo(name, extra_terms=()):
    """Best-effort (bytes, source_url) for a company's canonical logo."""
    tried = set()
    for term in (name,) + tuple(extra_terms):
        for hit in _search(term):
            qid, desc = hit["id"], hit.get("description", "")
            if qid in tried:
                continue
            tried.add(qid)
            if PERSONISH.search(desc) or not FIRMISH.search(desc):
                continue
            # The search index follows renames: "Farallon" resolves to Netopia,
            # which really was Farallon Computing once. Require the label we get
            # back to still contain the name we asked about.
            label = (hit.get("label") or "").lower()
            key = re.sub(r"[^a-z]", "", term.split()[0].lower())
            initials = "".join(w[0] for w in label.split() if w)
            if len(key) > 2 and key not in re.sub(r"[^a-z]", "", label) \
                    and key != initials[:len(key)]:
                continue        # KKR -> "Kohlberg Kravis Roberts" is still a match
            fn = _logo_file(qid)
            if not fn:
                continue
            url = FILEPATH + urllib.parse.quote(fn.replace(" ", "_")) + "?width=512"
            try:
                return _get(url, timeout=25), url
            except Exception:
                continue
    return article_logo(name)
