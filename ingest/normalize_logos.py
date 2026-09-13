"""Download, trim and normalise every logo to one consistent square asset.

Hotlinking whatever a site serves produced the mess: a 16px favicon stretched
to 44px reads as a smudge, an apple-touch-icon is often a rounded app tile
rather than the mark, and nothing lined up because every source had a different
aspect ratio and padding.

So: fetch once, trim the surrounding whitespace, centre on a white square with
even margin, and write a 128px PNG. The UI then serves uniform assets.
"""
import io, json, os, re, sys, urllib.request

from PIL import Image, ImageChops

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "ui", "assets", "logos")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36"}
SIZE, MARGIN = 128, 0.10


def fetch(url, timeout=20):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                  timeout=timeout).read()


def trim(im):
    """Drop uniform border so every mark gets the same optical margin."""
    bg = Image.new(im.mode, im.size, im.getpixel((0, 0)))
    diff = ImageChops.difference(im, bg)
    box = diff.getbbox()
    return im.crop(box) if box else im


def looks_blank(im):
    """A single-colour tile is a placeholder, not a logo."""
    small = im.convert("RGB").resize((16, 16))
    px = list(small.getdata())
    return len(set(px)) <= 2


def normalise(url, slug, min_px=64):
    try:
        raw = fetch(url)
    except Exception as e:
        return None, f"fetch failed ({type(e).__name__})"
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        return None, "undecodable (svg or corrupt)"
    if min(im.size) < min_px:
        return None, f"too small ({im.size[0]}x{im.size[1]})"
    im = im.convert("RGBA")
    white = Image.new("RGBA", im.size, (255, 255, 255, 255))
    im = Image.alpha_composite(white, im).convert("RGB")
    im = trim(im)
    if im.size[0] < 16 or im.size[1] < 16 or looks_blank(im):
        return None, "blank or featureless"
    inner = int(SIZE * (1 - 2 * MARGIN))
    im.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGB", (SIZE, SIZE), (255, 255, 255))
    canvas.paste(im, ((SIZE - im.size[0]) // 2, (SIZE - im.size[1]) // 2))
    os.makedirs(OUT, exist_ok=True)
    canvas.save(os.path.join(OUT, f"{slug}.png"), "PNG", optimize=True)
    return f"assets/logos/{slug}.png", "ok"


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


if __name__ == "__main__":
    ok = bad = 0
    for fname, key in (("firm_logos.json", "ticker"), ("fund_logos.json", None)):
        p = os.path.join(DATA, fname)
        if not os.path.exists(p):
            continue
        d = json.load(open(p))
        for name, v in d.items():
            url = v.get("logo")
            if not url:
                continue
            local, why = normalise(url, slugify(name))
            if local:
                v["logo_local"] = local
                v["sharp"] = True
                ok += 1
            else:
                v.pop("logo_local", None)
                v["sharp"] = False
                bad += 1
                print(f"  {name:22s} {why}")
        json.dump(d, open(p, "w"), indent=1)
    print(f"\n{ok} logos normalised, {bad} rejected (those fall back to a wordmark)")
