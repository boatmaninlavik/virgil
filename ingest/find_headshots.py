"""Headshots for principals with no free-licensed portrait.

Wikimedia, Wikidata P18 and the firms' own leadership pages are exhausted first
— they cover 32 of 77. The rest simply have no free image anywhere, so this
falls back to image search and hotlinks the result.

Two caveats worth keeping in mind. These are third-party press photos, not
free-licensed, so they are fine on a private personal dashboard and would need
proper licensing before any public product. And unlike the Wikipedia path,
nothing here verifies identity beyond the specificity of the query — the search
is name plus firm, and the top square result is taken on trust.
"""
import json, os, re, struct, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36"}
# Blacklisting bad sources was a losing game — Pinterest, a furniture retailer
# and a personal blog all slipped through. Whitelist credible ones instead:
# business press, exec-profile databases, and the person's own firm. Anything
# else falls back to a monogram, because a wrong face is worse than no face.
GOOD = re.compile(
    r"(upload\.wikimedia\.org|commons\.wikimedia\.org|theorg\.com"
    r"|forbes\.com|bloomberg\.com|reuters\.com|wsj\.net|wsj\.com|ft\.com"
    r"|cnbc\.com|fortune\.com|barrons\.com|businessinsider|institutionalinvestor"
    r"|nymag\.com|newyorker|economist\.com|marketwatch|axios\.com"
    r"|crunchbase|pitchbook|hbs\.edu|\.edu/|sec\.gov)", re.I)

BAD = re.compile(r"(logo|getty|alamy|shutterstock|watermark|stock-photo|/thumb/|sprite"
                 r"|podcast|episode|cover|artwork|album|poster|banner|book|webinar"
                 r"|conference|event|panel|award|screenshot|quote|card|badge"
                 r"|youtube|ytimg|spotify|apple.*podcast|libsyn|buzzsprout|megaphone"
                 r"|soundcloud|anchor\.fm|simplecast|feedshare|company-logo|activity-)", re.I)


def get(url, ref=None, timeout=20, limit=4_000_000):
    h = dict(UA)
    if ref:
        h["Referer"] = ref
    return urllib.request.urlopen(urllib.request.Request(url, headers=h),
                                  timeout=timeout).read(limit)


def vqd(query):
    html = get("https://duckduckgo.com/?q=" + urllib.parse.quote(query)
               + "&iax=images&ia=images").decode("utf-8", "replace")
    m = re.search(r'vqd=["\']?([\d-]+)', html)
    return m.group(1) if m else None


def search_images(query):
    token = vqd(query)
    if not token:
        return []
    u = (f"https://duckduckgo.com/i.js?l=us-en&o=json&q={urllib.parse.quote(query)}"
         f"&vqd={token}&f=,,,&p=1")
    d = json.loads(get(u, ref="https://duckduckgo.com/"))
    return d.get("results", [])


def pick(results):
    """Prefer a square, decently sized image — that is what a headshot looks like."""
    scored = []
    for r in results:
        url, w, h = r.get("image"), r.get("width") or 0, r.get("height") or 0
        if not url or BAD.search(url) or w < 300 or h < 300:
            continue
        ratio = h / w
        if ratio < 0.85 or ratio > 1.6:
            continue
        # closeness to square first, then resolution
        scored.append((abs(ratio - 1.0), -min(w, h), url, w, h))
    scored.sort()
    return scored[:4]


def verify(url):
    try:
        b = get(url, timeout=15)
    except Exception:
        return None
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", b[16:24])
    if b[:3] == b"\xff\xd8\xff":
        i = 2
        while i < len(b) - 9:
            if b[i] != 0xFF:
                i += 1
                continue
            if b[i + 1] in (0xC0, 0xC1, 0xC2, 0xC3):
                h, w = struct.unpack(">HH", b[i + 5:i + 9])
                return (w, h)
            if i + 4 > len(b):
                break
            i += 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return (500, 500)
    return None


if __name__ == "__main__":
    from logos import DOMAINS
    people = json.load(open(os.path.join(DATA, "people.json")))
    only = set(sys.argv[1:])
    todo = [(k, v) for k, v in people.items()
            if (not v.get("photo") or k in only) and v.get("firm")]
    if only:
        todo = [(k, v) for k, v in people.items() if k in only]
    print(f"{len(todo)} principals to search")
    found = 0
    for person, p in todo:
        if person in ("Baillie Gifford", "Jane Street", "Vanguard Advisers",
                      "Optiver", "IMC"):
            continue                       # partnerships, no single principal
        q = f"{person} {p.get('firm','')} executive portrait photo"
        try:
            res = search_images(q)
        except Exception as e:
            print(f"  {person:24s} search failed: {type(e).__name__}")
            time.sleep(2)
            continue
        got = None
        firm_host = (DOMAINS.get(p.get("firm")) or "").replace("www.", "")
        for _, _, url, w, h in pick(res):
            if BAD.search(url):
                continue
            # Source quality is now enforced by faces.py, which rejects anything
            # without a detectable face. A domain whitelist was a poor proxy for
            # that and cost most of the coverage.
            pass
            sz = verify(url)
            if sz:
                got = (url, sz)
                break
        if got:
            url, (w, h) = got
            ratio = h / w
            p["photo"] = url
            p["shape"] = {"w": w, "h": h, "ratio": round(ratio, 2),
                          "focus": 8 if ratio >= 1.6 else 14 if ratio >= 1.3 else 25}
            p["source"] = "image search (press photo, not free-licensed)"
            found += 1
            print(f"  {person:24s} {w}x{h}  {url[:66]}")
        else:
            print(f"  {person:24s} no usable result")
        json.dump(people, open(os.path.join(DATA, "people.json"), "w"), indent=2)
        time.sleep(1.5)
    print(f"\n{found} found; {sum(1 for v in people.values() if v.get('photo'))}"
          f"/{len(people)} now have a portrait")
