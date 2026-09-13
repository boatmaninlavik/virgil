"""Logos scraped from each firm's own site, with a real quality gate.

Brandfetch's free Brand API allows 100 calls and we have 169 domains, so it
403s partway through. Scraping is unlimited; the reason it produced smudges
before was that nothing checked the result.

Three gates, in order:
  size      — reject anything under 64px, which is what made logos blurry
  contrast  — composite onto whichever ground the mark is visible against,
              then reject tiles that still have no contrast. White-on-
              transparent logos flattened onto white is what made Point72,
              TPG, Goldman and Citadel disappear.
  shape     — near-square only for og:image, which is otherwise a hero photo
"""
import io, json, os, re, struct, sys, urllib.parse, urllib.request

# Homebrew's cairo is not on miniforge's library path, and cairosvg dlopens it
# at import. Set the fallback before that import or it fails on this machine.
os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")
try:
    import cairosvg
except Exception:
    cairosvg = None

from PIL import Image
import linkedin
import wikidata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "ui", "assets", "logos")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36"}
SIZE, MARGIN = 160, 0.10
# Gate on the long edge, not the short one. Wordmarks are wide by nature -
# Greenlight ships 202x63 and Baupost 440x29, both perfectly sharp, and a
# min()-based gate threw them out while letting 64x64 favicons through.
MIN_LONG, MIN_SHORT = 120, 16

ICON = re.compile(r"""<link[^>]+rel=["'][^"']*\b(?:apple-touch-icon|icon|mask-icon)\b[^"']*["'][^>]*>""", re.I)
HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)
SIZES = re.compile(r"""sizes=["'](\d+)x(\d+)["']""", re.I)
OG = re.compile(r"""<meta[^>]+property=["']og:image["'][^>]+content=["']([^"']+)["']""", re.I)
IMGTAG = re.compile(r"<img[^>]+>", re.I)
SRC = re.compile(r"""\ssrc=["']([^"']+)["']""", re.I)
# Third Point ships its real mark as <img src="/img/ui/logo.svg" alt="Third
# Point Logo"> while its apple-touch-icon is an unedited placeholder reading
# "Apple Touch Icon", so the <img> is the thing worth finding.
LOGOISH = re.compile(r"logo|brand|wordmark", re.I)
NOTLOGO = re.compile(r"sprite|placeholder|avatar|photo|hero|banner|icon-", re.I)


def get(url, timeout=14, limit=3_000_000):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                  timeout=timeout).read(limit)


def candidates(domain):
    """Declared icons first (largest declared size wins), then a logo-named
    <img>, then og:image as a last resort."""
    out = []
    for scheme in ("https://www.", "https://"):
        base = scheme + domain
        try:
            home = get(base).decode("utf-8", "replace")
        except Exception:
            continue
        for tag in ICON.findall(home):
            h = HREF.search(tag)
            if not h:
                continue
            sz = SIZES.search(tag)
            out.append((int(sz.group(1)) if sz else 0,
                        urllib.parse.urljoin(base + "/", h.group(1)), False))
        # A footer is full of other companies' logos - the CMS agency that built
        # the site, the prime broker, the custodian - and they match "logo" just
        # as well as the brand does. Farallon's tile became the web agency
        # "netopia" and Walleye's became Northern Trust. So score on position,
        # and boost any asset that names the firm itself.
        own = re.split(r"[.-]", domain)[0].lower()
        for m in list(IMGTAG.finditer(home))[:120]:
            tag, at = m.group(0), m.start() / max(1, len(home))
            src = SRC.search(tag)
            if not src or not LOGOISH.search(tag) or NOTLOGO.search(tag):
                continue
            u = urllib.parse.urljoin(base + "/", src.group(1))
            if not re.search(r"\.(svg|png|jpe?g|webp)(\?|$)", u, re.I):
                continue
            # Match the filename, not the whole URL: every asset on
            # walleyecapital.com contains "walley" in its host, which made the
            # custodian's Northern_Trust_logo.jpeg score as Walleye's own mark.
            leaf = urllib.parse.urlparse(u).path.rsplit("/", 1)[-1].lower()
            alt = (re.search(r'alt=["\']([^"\']*)', tag, re.I) or
                   re.match("", "")).group(1).lower() if 'alt=' in tag.lower() else ""
            named = own[:6] in leaf or own[:6] in alt
            if at > 0.55 and not named:
                continue                 # footer, and nothing says it is ours
            out.append((190 if named else 150 - int(at * 40), u, False))
        og = OG.search(home)
        if og:
            out.append((-1, urllib.parse.urljoin(base + "/", og.group(1)), True))
        break
    out.append((-2, f"https://www.google.com/s2/favicons?domain={domain}&sz=256", False))
    return [(u, is_og) for _, u, is_og in sorted(out, key=lambda x: -x[0])]


# Some CMS themes ship an unedited placeholder as the site's apple-touch-icon.
# They are byte-identical across sites, so a content hash rejects them outright.
JUNK_MD5 = {
    "f080d41b68316fab9fe33c18d4b3d690",   # "Apple Touch Icon" grey placeholder
}


def _junk(raw):
    import hashlib
    return hashlib.md5(raw).hexdigest() in JUNK_MD5


def _modal(colors):
    """Most common colour in a sample, and what fraction of it that is."""
    from collections import Counter
    c = Counter(colors).most_common(1)[0]
    return c[0], c[1] / float(len(colors))


def _photo(im):
    """A photograph, not a mark: many colours and no flat ground anywhere.

    "The TPG Take" — a podcast cover — slipped through the old aspect-ratio
    gate because it happens to be near-square. Logos essentially always sit on
    a flat ground; photographs essentially never do.
    """
    sm = im.convert("RGB").resize((32, 32), Image.LANCZOS)
    px = list(sm.getdata())
    _, frac = _modal(px)
    # A gradient wordmark can carry hundreds of colours without being a photo,
    # so also require the image to lack any sizeable flat region at all.
    return len(set(px)) > 300 and frac < 0.08


def _sat(rgb):
    return (max(rgb) - min(rgb)) / 255.0


def render(raw, slug):
    """Fit the mark to a square filled with its own ground. Returns (path, chip).

    The old version pasted light marks onto near-black, which on a light page
    read as a black-bordered tile — 34 of them. Ground now comes from the
    source itself: an opaque image keeps the background it shipped with, and a
    cut-out mark gets white, inverting a white-on-transparent wordmark to dark
    rather than framing it in black.
    """
    if raw[:400].lstrip()[:5].lower() in (b"<svg ", b"<svg>", b"<?xml") and cairosvg:
        try:
            raw = cairosvg.svg2png(bytestring=raw, output_width=512)
        except Exception:
            return None, None, "unrasterisable svg"
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        return None, None, "undecodable"
    if _junk(raw):
        return None, None, "known placeholder asset"
    if max(im.size) < MIN_LONG or min(im.size) < MIN_SHORT:
        return None, None, f"too small ({im.size[0]}x{im.size[1]})"
    if _photo(im):
        return None, None, "photograph"
    im = im.convert("RGBA")
    box = im.getbbox()
    if box:
        im = im.crop(box)
    if min(im.size) < 12:
        return None, None, "empty"

    alpha, rgb = im.getchannel("A"), im.convert("RGB")
    opaque = sum(1 for a in alpha.getdata() if a > 200) / float(alpha.size[0] * alpha.size[1])
    px = [p for p, a in zip(rgb.getdata(), alpha.getdata()) if a > 40]
    if not px:
        return None, None, "fully transparent"

    if opaque > 0.92:
        # Shipped with its own background - keep it, and trim any uniform
        # border so the mark is centred rather than floating in its own frame.
        w, h = im.size
        ring = ([rgb.getpixel((x, 0)) for x in range(w)] +
                [rgb.getpixel((x, h - 1)) for x in range(w)] +
                [rgb.getpixel((0, y)) for y in range(h)] +
                [rgb.getpixel((w - 1, y)) for y in range(h)])
        ground, frac = _modal(ring)
        if frac < 0.5:
            ground = (255, 255, 255)
        flat = rgb
    else:
        lum = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in px) / len(px)
        sat = sum(_sat(p) for p in px) / len(px)
        if lum > 150 and sat < 0.18:
            # A white wordmark has no readable form on white and no brand
            # colour to fall back on, so invert it instead of boxing it black.
            inv = Image.eval(rgb, lambda v: 255 - v)
            im = Image.merge("RGBA", (*inv.split(), alpha))
            ground = (255, 255, 255)
        elif lum > 150:
            # Light but coloured: tint the tile with the mark's own hue, which
            # is what "fill it naturally with the logo's colour" means here.
            dom, _ = _modal([p for p in px if _sat(p) > 0.25] or px)
            ground = tuple(int(c * 0.42) for c in dom)
        else:
            ground = (255, 255, 255)
        flat = Image.alpha_composite(
            Image.new("RGBA", im.size, ground + (255,)), im).convert("RGB")

    inner = int(SIZE * (1 - 2 * MARGIN))
    flat = flat.copy()
    flat.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGB", (SIZE, SIZE), ground)
    canvas.paste(flat, ((SIZE - flat.size[0]) // 2, (SIZE - flat.size[1]) // 2))
    if len(set(canvas.resize((24, 24)).convert("RGB").getdata())) <= 3:
        return None, None, "no contrast"

    gl = 0.299 * ground[0] + 0.587 * ground[1] + 0.114 * ground[2]
    os.makedirs(OUT, exist_ok=True)
    canvas.save(os.path.join(OUT, f"{slug}.png"), "PNG", optimize=True)
    return f"assets/logos/{slug}.png", ("dark" if gl < 128 else "light"), "ok"


def best(domain, slug, name=None):
    """Wikidata first, then the firm's own site.

    A firm's site ships whatever it ships: Third Point's apple-touch-icon is an
    unedited placeholder reading "Apple Touch Icon". Wikidata's P154 claim is a
    curated pointer to the real mark, so try it before trusting the site.
    """
    if name:
        try:
            raw, _ = wikidata.logo(name, (f"{name} Management", f"{name} Capital"))
        except Exception:
            raw = None
        if raw:
            path, chip, why = render(raw, slug)
            if path:
                return path, chip
    # LinkedIn before the firm's own site: it has a square mark for nearly
    # every fund, on a page that does not block us, and square is what the tile
    # wants anyway. ark-funds.com answers 403; linkedin.com/company/ark-
    # investment-management does not.
    if name:
        try:
            raw, _ = linkedin.logo(name)
        except Exception:
            raw = None
        if raw:
            path, chip, why = render(raw, slug)
            if path:
                return path, chip

    for url, is_og in candidates(domain):
        try:
            raw = get(url)
        except Exception:
            continue
        if raw[:400].lstrip()[:5].lower() not in (b"<svg ", b"<svg>", b"<?xml"):
            try:
                probe = Image.open(io.BytesIO(raw))
                w, h = probe.size
            except Exception:
                continue
            if is_og and max(w, h) / max(1, min(w, h)) > 1.35:
                continue                 # a wide og:image is a hero photo
        path, chip, why = render(raw, slug)
        if path:
            return path, chip
    return None, None


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


if __name__ == "__main__":
    import argparse
    from concurrent.futures import ThreadPoolExecutor
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from logos import DOMAINS, FIRM_TICKERS

    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="substring filter, for spot checks")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()

    names = {t: v["name"] for t, v in
             json.load(open(os.path.join(DATA, "ciks.json")))["firms"].items()}

    def work(item):
        key, dom, disp = item
        try:
            return key, best(dom, slugify(key), disp)
        except Exception as e:
            return key, (None, None)

    jobs = ([(t, d, names.get(t, t)) for t, d in FIRM_TICKERS.items()] +
            [(n, d, n) for n, d in DOMAINS.items()])
    if a.only:
        jobs = [j for j in jobs if a.only.lower() in j[0].lower()]
    firm_keys = set(FIRM_TICKERS)

    firm = json.load(open(os.path.join(DATA, "firm_logos.json")))
    fund = json.load(open(os.path.join(DATA, "fund_logos.json")))
    ok = bad = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for key, (path, chip) in ex.map(work, jobs):
            dom = dict((j[0], j[1]) for j in jobs)[key]
            rec = {"domain": dom, "logo_local": path, "sharp": bool(path),
                   "chip": chip}
            if key in firm_keys:
                rec["ticker"] = key
                firm[key] = rec
            else:
                fund[key] = rec
            ok, bad = (ok + 1, bad) if path else (ok, bad + 1)
            print(f"  {key[:26]:28s} {dom[:24]:26s} "
                  f"{('ok ' + (chip or '')) if path else 'none'}", flush=True)

    json.dump(firm, open(os.path.join(DATA, "firm_logos.json"), "w"), indent=1)
    json.dump(fund, open(os.path.join(DATA, "fund_logos.json"), "w"), indent=1)
    print(f"\n{ok} logos rendered, {bad} without a usable asset")
