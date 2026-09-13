"""Keep bulk caches in GCS instead of on local disk.

Not everything can leave: the page generator reads all 138 funds' holdings on
every build, so those stay local. What ballooned was the market-wide Form 4
scan cache — one entry per filing, ~1,400 filings a trading day — which grows
past 100 MB over a 20-day window and is only ever read by the scanner itself.

So: the scanner streams its cache to the bucket after each day and keeps only
the derived index locally, which is a thousandth of the size.
"""
import json, os, subprocess, tempfile

BUCKET = os.environ.get("VIRGIL_BUCKET", "gs://virgil-edgar")
PREFIX = "store"


def _uri(name):
    return f"{BUCKET}/{PREFIX}/{name}"


def put_json(name, obj):
    """Write a JSON blob straight to GCS, gzipped, leaving no local copy."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(obj, fh, separators=(",", ":"))
        tmp = fh.name
    try:
        r = subprocess.run(["gcloud", "storage", "cp", "-Z", tmp, _uri(name),
                            "--content-type=application/json", "--quiet"],
                           capture_output=True, text=True)
        return r.returncode == 0
    finally:
        os.unlink(tmp)


def get_json(name, default=None):
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as fh:
        tmp = fh.name
    try:
        r = subprocess.run(["gcloud", "storage", "cp", _uri(name), tmp, "--quiet"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return default
        with open(tmp) as fh:
            return json.load(fh)
    except Exception:
        return default
    finally:
        os.path.exists(tmp) and os.unlink(tmp)


def size_mb(path):
    if os.path.isfile(path):
        return os.path.getsize(path) / 1048576
    t = 0
    for root, _, files in os.walk(path):
        t += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    return t / 1048576
