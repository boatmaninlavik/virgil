"""Logos via Brandfetch, replacing the favicon scraping entirely.

The old pipeline guessed: apple-touch-icons, og:images, 16px favicons. That
produced blurry marks, app tiles, and once a photo of a video wall for TPG.
Brandfetch is built for this — it returns the actual brand asset, so there is
nothing to guess.

Prefers the square symbol for the avatar slot, falling back to the full
wordmark. Assets are still normalised locally so every logo lands on the same
canvas with the same optical margin.
"""
import io, json, os, re, sys, urllib.request

from PIL import Image, ImageChops

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "ui", "assets", "logos")
CREDS = os.path.expanduser("~/.virgil/credentials")
SIZE, MARGIN = 160, 0.10


def key():
    for line in open(CREDS):
        if line.startswith("BRANDFETCH_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no BRANDFETCH_API_KEY in ~/.virgil/credentials")


def brand(domain, k):
    req = urllib.request.Request(f"https://api.brandfetch.io/v2/brands/{domain}",
                                 headers={"Authorization": f"Bearer {k}"})
    return json.loads(urllib.request.urlopen(req, timeout=25).read())


def pick_asset(payload):
    """Square symbol first — it fits an avatar slot; wordmark as fallback."""
    logos = payload.get("logos") or []
    def fmts(kind, theme):
        for L in logos:
            if L.get("type") != kind:
                continue
            if theme and L.get("theme") not in (theme, None):
                continue
            best = None
            for f in L.get("formats", []):
                if not f.get("src"):
                    continue
                # prefer raster we can decode; svg only if nothing else
                if f.get("format") in ("png", "jpeg", "jpg", "webp"):
                    return f["src"]
                best = best or f["src"]
            if best:
                return best
        return None
    for kind in ("symbol", "icon", "logo"):
        for theme in ("light", None, "dark"):
            src = fmts(kind, theme)
            if src:
                return src
    return None


def trim(im):
    bg = Image.new(im.mode, im.size, im.getpixel((0, 0)))
    box = ImageChops.difference(im, bg).getbbox()
    return im.crop(box) if box else im


def _prep(im):
    im = im.convert("RGBA")
    box = im.getbbox()               # trim transparent margin before measuring
    if box:
        im = im.crop(box)
    return im


def normalise(url, slug):
    """Place the mark on whichever ground it is actually visible against.

    Many brand assets are white-on-transparent. Composited onto white — which
    is what the old code did unconditionally — they came out as blank tiles;
    that is why Point72, TPG, Goldman and Citadel went missing.
    """
    try:
        raw = urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "virgil/0.1"}), timeout=25).read()
        im = Image.open(io.BytesIO(raw)); im.load()
    except Exception as e:
        return None, f"{type(e).__name__}", None
    im = _prep(im)
    if min(im.size) < 12:
        return None, "empty", None

    alpha = im.getchannel("A")
    rgb = im.convert("RGB")
    # mean luminance of the pixels that are actually part of the mark
    px = [p for p, a in zip(rgb.getdata(), alpha.getdata()) if a > 40]
    if not px:
        return None, "fully transparent", None
    lum = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in px) / len(px)
    ground = (17, 19, 17) if lum > 150 else (255, 255, 255)
    chip = "dark" if lum > 150 else "light"

    flat = Image.alpha_composite(Image.new("RGBA", im.size, ground + (255,)), im).convert("RGB")
    inner = int(SIZE * (1 - 2 * MARGIN))
    flat.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGB", (SIZE, SIZE), ground)
    canvas.paste(flat, ((SIZE - flat.size[0]) // 2, (SIZE - flat.size[1]) // 2))

    # a tile with no contrast is useless whatever the reason
    probe = canvas.resize((24, 24)).convert("RGB")
    vals = list(probe.getdata())
    if len(set(vals)) <= 3:
        return None, "no contrast", None

    os.makedirs(OUT, exist_ok=True)
    canvas.save(os.path.join(OUT, f"{slug}.png"), "PNG", optimize=True)
    return f"assets/logos/{slug}.png", "ok", chip


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from logos import DOMAINS, FIRM_TICKERS
    k = key()
    ok = bad = 0

    firm = {}
    for tkr, dom in FIRM_TICKERS.items():
        try:
            src = pick_asset(brand(dom, k))
        except Exception as e:
            src = None
        local, why, chip = normalise(src, slugify(tkr)) if src else (None, "no asset", None)
        firm[tkr] = {"ticker": tkr, "domain": dom, "logo": src,
                     "logo_local": local, "sharp": bool(local), "chip": chip}
        ok, bad = (ok + 1, bad) if local else (ok, bad + 1)
        print(f"  {tkr:6s} {dom:24s} {'ok' if local else why}")
    json.dump(firm, open(os.path.join(DATA, "firm_logos.json"), "w"), indent=1)

    fund = {}
    for name, dom in DOMAINS.items():
        try:
            src = pick_asset(brand(dom, k))
        except Exception:
            src = None
        local, why, chip = normalise(src, slugify(name)) if src else (None, "no asset", None)
        fund[name] = {"domain": dom, "logo": src, "logo_local": local,
                      "sharp": bool(local), "chip": chip}
        ok, bad = (ok + 1, bad) if local else (ok, bad + 1)
    json.dump(fund, open(os.path.join(DATA, "fund_logos.json"), "w"), indent=1)
    print(f"\n{ok} logos from Brandfetch, {bad} without an asset")
