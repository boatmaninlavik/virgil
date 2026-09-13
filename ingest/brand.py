"""High-resolution logos for tracked firms.

Google's favicon service returns whatever the site declares, and several firms
(BlackRock, Third Point, Wellington) only publish a 16x16 icon — upscaled into
a 44px slot that reads as a blur. So: parse each homepage for its declared
icons, take the largest, and record the real pixel size so the UI can fall
back to a wordmark rather than display something soft.
"""
import json, os, re, struct, sys, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
ICON_RE = re.compile(
    r"""<link[^>]+rel=["'][^"']*\b(?:apple-touch-icon|icon|shortcut icon)\b[^"']*["'][^>]*>""",
    re.I)
HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)
SIZES_RE = re.compile(r"""sizes=["'](\d+)x(\d+)["']""", re.I)
OG_RE = re.compile(r"""<meta[^>]+property=["']og:image["'][^>]+content=["']([^"']+)["']""", re.I)


def get(url, timeout=12, limit=400_000):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)
    return r.read(limit)


def image_size(b):
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", b[16:24])
    if b"<svg" in b[:400].lower():
        return (1000, 1000)                     # vector: treat as high res
    if b[:3] == b"\xff\xd8\xff":                # minimal JPEG SOF scan
        i = 2
        while i < len(b) - 9:
            if b[i] != 0xFF:
                i += 1
                continue
            m = b[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3):
                h, w = struct.unpack(">HH", b[i + 5:i + 9])
                return (w, h)
            if i + 4 > len(b):
                break
            i += 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
    return None


def candidates(domain):
    urls = []
    for scheme in ("https://www.", "https://"):
        try:
            home = get(scheme + domain).decode("utf-8", "replace")
        except Exception:
            continue
        base = scheme + domain
        for tag in ICON_RE.findall(home):
            h = HREF_RE.search(tag)
            if not h:
                continue
            s = SIZES_RE.search(tag)
            px = int(s.group(1)) if s else 0
            urls.append((px, urllib.parse.urljoin(base + "/", h.group(1)), False))
        # og:image last: for some firms it IS the square logo (BlackRock), for
        # others a wide hero photo (TPG). The aspect test in best_logo decides.
        og = OG_RE.search(home)
        if og:
            urls.append((-1, urllib.parse.urljoin(base + "/", og.group(1)), True))
        break
    urls.append((-2, f"https://www.google.com/s2/favicons?domain={domain}&sz=128", False))
    # declared size first, then discovered size
    return [(u, is_og) for _, u, is_og in sorted(urls, key=lambda x: -x[0])]


def best_logo(domain, min_px=48, max_ratio=2.6, og_max_ratio=1.35):
    for url, is_og in candidates(domain):
        try:
            b = get(url)
        except Exception:
            continue
        sz = image_size(b)
        if not sz:
            continue
        w, h = sz
        ratio = max(w, h) / max(1, min(w, h))
        limit = og_max_ratio if is_og else max_ratio
        if ratio > limit:              # wide banner or photo, not a mark
            continue
        if w >= min_px and h >= min_px:
            return {"logo": url, "w": w, "h": h, "sharp": True,
                    "wide": (w / max(1, h)) >= 1.6,
                    "kind": "svg" if w == 1000 and h == 1000 else "raster"}
    return {"logo": f"https://www.google.com/s2/favicons?domain={domain}&sz=128",
            "w": 0, "h": 0, "sharp": False, "wide": False, "kind": "favicon"}


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from logos import DOMAINS, FIRM_TICKERS

    out = {}
    for tkr, dom in FIRM_TICKERS.items():
        out[tkr] = {"ticker": tkr, "domain": dom, **best_logo(dom)}
        b = out[tkr]
        print(f"  {tkr:6s} {dom:24s} {b['w']}x{b['h']:<5} {'sharp' if b['sharp'] else 'LOW-RES'}")
    json.dump(out, open(os.path.join(DATA, "firm_logos.json"), "w"), indent=1)

    fund = {}
    for name, dom in DOMAINS.items():
        fund[name] = {"domain": dom, **best_logo(dom)}
    json.dump(fund, open(os.path.join(DATA, "fund_logos.json"), "w"), indent=1)
    sharp = sum(1 for v in {**out, **fund}.values() if v["sharp"])
    print(f"\n{sharp}/{len(out)+len(fund)} logos at usable resolution")
