"""Verify and normalise portraits with actual face detection.

Every previous attempt sourced images and hoped. That is how a map ended up on
Two Sigma's CEO and a podcast cover on John Graham: nothing in the pipeline ever
looked at the pixels.

This does. An image is only accepted if OpenCV finds a face occupying a
sensible share of the frame. Accepted images are then cropped around the face
and written as a uniform square, so the UI serves consistent assets instead of
hotlinking arbitrary aspect ratios and asking CSS to rescue them.
"""
import io, os, sys, urllib.request

import cv2
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "ui", "assets", "faces")
# Wikimedia rejects generic browser strings and wants a descriptive agent;
# most other hosts want a browser-like one. Send the right thing per host.
UA_WIKI = {"User-Agent": "virgil/0.1 (sean@erised.me) portrait-fetch"}
UA_WEB = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
          "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
          "Accept-Language": "en-US,en;q=0.9"}
SIZE = 256

_cascade = None
_profile = None


def cascade():
    global _cascade, _profile
    if _cascade is None:
        base = cv2.data.haarcascades
        _cascade = cv2.CascadeClassifier(base + "haarcascade_frontalface_default.xml")
        _profile = cv2.CascadeClassifier(base + "haarcascade_profileface.xml")
    return _cascade, _profile


MAX_BYTES = 6_000_000       # a headshot is never 20 MB; a poster might be
MAX_EDGE = 1400             # decode cap: 6000px of press photo is 100 MB of BGR


def download(url, timeout=25):
    h = UA_WIKI if ("wikimedia.org" in url or "wikipedia.org" in url) else UA_WEB
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("image too large")
    return raw


def shrink(raw):
    """Downscale before OpenCV sees it.

    cv2.imdecode allocates width*height*3 bytes. A batch of press photos at
    6000x4000 is 72 MB each, which is what exhausted memory and took the
    machine down. Nothing here needs more than ~1400px on the long edge.
    """
    try:
        im = Image.open(io.BytesIO(raw))
        im.draft("RGB", (MAX_EDGE, MAX_EDGE))     # JPEG: decode at 1/2, 1/4...
        if im.mode in ("RGBA", "LA", "P"):
            # A cut-out portrait on transparency turns black in cv2 and leaves
            # dark wedges in the corners of the crop, as it did for Rob Sharps.
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, im)
        im = im.convert("RGB")
        if max(im.size) > MAX_EDGE:
            im.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=92)
        im.close()
        return buf.getvalue()
    except Exception:
        return raw


def skin_ratio(bgr, box):
    """Share of the face box that reads as skin.

    A photographed pencil drawing or an engraving passes face detection — the
    drawn face is a face — but has no skin tone. Real headshots do.
    """
    x, y, w, h = box
    patch = bgr[y:y + h, x:x + w]
    if patch.size == 0:
        return 0.0
    ycrcb = cv2.cvtColor(patch, cv2.COLOR_BGR2YCrCb)
    lo = np.array([0, 133, 77], np.uint8)
    hi = np.array([255, 173, 127], np.uint8)
    return float(cv2.inRange(ycrcb, lo, hi).mean()) / 255.0


def border_ratio(bgr, tol=14):
    """Share of the frame that is a flat uniform border.

    A photo of a framed print is mostly wall and mat; a headshot is not.
    """
    h, w = bgr.shape[:2]
    edge = np.concatenate([bgr[0, :], bgr[-1, :], bgr[:, 0], bgr[:, -1]])
    ref = np.median(edge, axis=0)
    flat = (np.abs(bgr.astype(int) - ref).max(axis=2) <= tol)
    return float(flat.mean())


def detect(img_bytes, min_frac=0.035):
    """Return the largest face box, or None. min_frac guards against a face
    that is incidental — a crowd shot or a logo with a tiny person in it."""
    arr = np.frombuffer(img_bytes, np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        return None, None
    h, w = bgr.shape[:2]
    if min(h, w) < 120:
        return None, None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    front, prof = cascade()
    boxes = list(front.detectMultiScale(gray, 1.1, 6, minSize=(60, 60)))
    if not boxes:
        boxes = list(prof.detectMultiScale(gray, 1.1, 6, minSize=(60, 60)))
    if not boxes:
        return None, bgr
    x, y, fw, fh = max(boxes, key=lambda b: b[2] * b[3])
    box = (int(x), int(y), int(fw), int(fh))
    if (fw * fh) / float(w * h) < min_frac:
        return None, bgr                      # incidental face, not a portrait
    # a real portrait has the face near the upper-middle, not off in a corner
    cx = (x + fw / 2.0) / w
    if cx < 0.12 or cx > 0.88:
        return None, bgr
    # No flat-border test: a corporate headshot on a plain studio backdrop is
    # exactly what we want, and it scores as "mostly uniform border". The
    # skin-tone check below is what actually rejects drawings and engravings.
    if skin_ratio(bgr, box) < 0.22:
        return None, bgr                      # drawing, engraving, or heavy graphic
    return box, bgr


def crop_square(bgr, box, size=SIZE):
    """Crop around the face with headroom above and shoulders below."""
    h, w = bgr.shape[:2]
    x, y, fw, fh = box
    cx, cy = x + fw / 2.0, y + fh / 2.0
    side = fh * 2.6                      # face fills ~38% of the frame
    side = max(side, fw * 2.6)
    side = min(side, min(h, w))
    top = cy - side * 0.46               # bias upward: headroom, not chin
    left = cx - side / 2.0
    left = max(0, min(left, w - side))
    top = max(0, min(top, h - side))
    crop = bgr[int(top):int(top + side), int(left):int(left + side)]
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    im = Image.fromarray(rgb).resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue()


def caption_band(bgr, floor=0.55):
    """Is there a burned-in caption across the lower part of the frame?

    Broadcast stills carry a lower third - "THE CREDIT PLAYBOOK", a name strap -
    and cropping to the face keeps it. Text is many strong vertical strokes
    packed into a few rows, so a row-wise stroke count spikes where the caption
    is and stays flat on a real photograph. Measured: captioned frames score
    3.6-7.9 against the image's own median, clean ones 1.3-2.4.
    """
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = g.shape
    if h < 40 or w < 40:
        return 0.0
    gx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
    strokes = (gx > 55).sum(axis=1) / float(w)
    med = float(np.median(strokes)) + 1e-6
    return float(strokes[int(h * floor):].max() / med)


def process(url, slug, min_frac=0.035, min_src=0, max_band=0.0):
    """Fetch, verify a face is present, crop, and write a square asset."""
    try:
        raw = download(url)
    except Exception:
        return None, "fetch failed"
    if min_src:
        # A 150px source upscaled into a 256px tile is visibly mushy - that is
        # what made David Einhorn's crop look pixelated.
        try:
            probe = Image.open(io.BytesIO(raw))
            if min(probe.size) < min_src:
                return None, f"source too small ({probe.size[0]}x{probe.size[1]})"
        except Exception:
            return None, "undecodable"
    raw = shrink(raw)
    box, bgr = detect(raw, min_frac=min_frac)
    if bgr is None:
        return None, "undecodable"
    if box is None:
        return None, "no face detected"
    crop = crop_square(bgr, box)
    if max_band:
        arr = cv2.imdecode(np.frombuffer(crop, np.uint8), cv2.IMREAD_COLOR)
        if arr is not None and caption_band(arr) > max_band:
            return None, "caption burned into the frame"
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{slug}.jpg")
    with open(path, "wb") as fh:
        fh.write(crop)
    return f"assets/faces/{slug}.jpg", "ok"


if __name__ == "__main__":
    import json, re
    people = json.load(open(os.path.join(DATA, "people.json")))
    kept = dropped = 0
    for name, p in sorted(people.items()):
        url = p.get("photo")
        if not url:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        local, why = process(url, slug)
        if local:
            p["photo_local"] = local
            p.pop("shape", None)          # cropping is done at build time now
            kept += 1
        else:
            p.pop("photo", None)
            p.pop("photo_local", None)
            dropped += 1
            print(f"  rejected {name:26s} {why}")
    json.dump(people, open(os.path.join(DATA, "people.json"), "w"), indent=2)
    print(f"\n{kept} portraits verified and cropped, {dropped} rejected")
