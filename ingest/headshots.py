"""Fetch headshots one person at a time, under a hard memory ceiling.

The previous run took the laptop down: six workers each spawning headless
Chrome, every one holding a few hundred megabytes, while OpenCV decoded press
photos at full resolution. This does the same job serially, never starts
Chrome, caps what it will decode, and checks free memory before each person -
pausing, and finally stopping, rather than pushing the machine into swap.

Everything it writes is committed immediately, so stopping costs nothing: rerun
and it resumes with whoever is still missing.

    python3 ingest/headshots.py                 # everyone missing
    python3 ingest/headshots.py --only loeb     # one person
    python3 ingest/headshots.py --limit 20      # a batch, then stop
"""
import gc, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import faces
import imagesearch
from logos import DOMAINS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEOPLE = os.path.join(ROOT, "data", "people.json")
FLOOR_MB = 1200          # stop entirely below this much free+inactive
PAUSE_MB = 2200          # breathe until it recovers above this


def free_mb():
    """Free + inactive + speculative pages, i.e. what the OS can hand out."""
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:
        return 1 << 30
    page = 4096
    m = re.search(r"page size of (\d+)", out)
    if m:
        page = int(m.group(1))
    got = {}
    for key in ("Pages free", "Pages inactive", "Pages speculative",
                "Pages purgeable"):
        mm = re.search(rf"{key}:\s+(\d+)", out)
        got[key] = int(mm.group(1)) if mm else 0
    return sum(got.values()) * page / 1048576


def wait_for_memory(verbose=True):
    """True to proceed, False to stop the run."""
    mb = free_mb()
    if mb >= PAUSE_MB:
        return True
    for _ in range(6):
        if verbose:
            print(f"   .. {mb:.0f} MB free, waiting", flush=True)
        gc.collect()
        time.sleep(10)
        mb = free_mb()
        if mb >= PAUSE_MB:
            return True
    return mb >= FLOOR_MB


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def one(person, rec, used):
    """(photo_local, source, why) for a single principal. No subprocesses."""
    slug = slugify(person)
    if rec.get("photo") and not rec.get("photo_local"):
        local, _ = faces.process(rec["photo"], slug, min_src=260, max_band=3.2)
        if local:
            return local, rec.get("wiki") or "wikipedia", "ok"
    return imagesearch.find(person, rec.get("firm") or "", slug, used=used)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args()

    people = json.load(open(PEOPLE))
    # Baillie Gifford, Jane Street, Optiver and Vanguard Advisers are
    # partnerships with no single principal; they carry a company logo, not a
    # face, so searching for a headshot of them finds someone else's.
    todo = [(k, v) for k, v in sorted(people.items())
            if (a.redo or not v.get("photo_local")) and not v.get("is_firm")]
    if a.only:
        todo = [t for t in todo if a.only.lower() in t[0].lower()]
    if a.limit:
        todo = todo[:a.limit]
    used = {v.get("photo_source") for v in people.values() if v.get("photo_source")}
    print(f"{len(todo)} to fetch · {free_mb():.0f} MB free\n", flush=True)

    found = 0
    for i, (person, rec) in enumerate(todo, 1):
        if not wait_for_memory():
            print(f"\nstopping at {i - 1}/{len(todo)}: memory is low. "
                  f"Rerun to resume.", flush=True)
            break
        try:
            local, source, why = one(person, rec, used)
        except Exception as e:
            local, source, why = None, None, type(e).__name__
        if local:
            people[person]["photo_local"] = local
            people[person]["photo_source"] = source
            used.add(source)
            found += 1
            json.dump(people, open(PEOPLE, "w"), indent=2)   # commit as we go
        print(f"{'OK ' if local else '-- '}{person:26s} "
              f"{(source or why)[:58]}", flush=True)
        gc.collect()

    have = sum(1 for v in people.values() if v.get("photo_local"))
    print(f"\n{found} new · {have}/{len(people)} have a headshot · "
          f"{free_mb():.0f} MB free")
