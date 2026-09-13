"""Headshots for principals with no Wikipedia article.

photos.py covers the notable ones: it resolves an English Wikipedia biography
and only accepts it when the article both matches the surname and reads as
finance, which is what stopped an actor's face landing on Chase Coleman's card.
That leaves everyone who simply has no article — most of the list.

For them the firm's own team page is the source. bios.py finds the person's own
page on the firm site by matching their name against the URL slug, then takes
its og:image; faces.py has to agree there is an actual face in the result
before it is kept, so a header image or an office photo cannot slip through.

    python3 ingest/portraits.py            # everyone still missing
    python3 ingest/portraits.py --only loeb
"""
import json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bios
import faces
import imagesearch
from logos import DOMAINS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEOPLE = os.path.join(ROOT, "data", "people.json")


USED = set()          # one image answers for exactly one person


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def acquire(person, rec, verbose=True):
    """Return (photo_local, source, why) for one principal."""
    slug = slugify(person)

    # A Wikipedia URL photos.py already verified: just needs cropping.
    if rec.get("photo") and not rec.get("photo_local"):
        local, why = faces.process(rec["photo"], slug)
        if local:
            return local, rec.get("wiki") or "wikipedia", "ok"

    firm = rec.get("firm") or ""
    domain = DOMAINS.get(firm)
    if domain:
        local, src, why = bios.find(person, domain)
        if local:
            return local, src, why
    # Last resort, and the only source for the many principals whose firm
    # publishes no roster and who have no free Wikipedia photo.
    return imagesearch.find(person, firm, slug, used=USED)


if __name__ == "__main__":
    import argparse
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor

    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="substring filter")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--no-bios", action="store_true",
                    help="skip the firm-site crawl; these firms publish no "
                         "roster and re-proving that costs minutes per person")
    a = ap.parse_args()

    people = json.load(open(PEOPLE))
    todo = [(k, v) for k, v in sorted(people.items())
            if a.redo or not v.get("photo_local")]
    if a.only:
        todo = [t for t in todo if a.only.lower() in t[0].lower()]

    # Group by firm: the roster crawl is the expensive part and it is the same
    # for every principal at the same firm.
    by_dom = defaultdict(list)
    for person, rec in todo:
        d = DOMAINS.get(rec.get("firm") or "")
        if d:
            by_dom[d].append(person)
    print(f"{len(todo)} principals across {len(by_dom)} firm sites\n", flush=True)

    found = [0]

    def do_firm(item):
        domain, names = item
        out = []
        try:
            links = {} if a.no_bios else bios.roster(domain)
        except Exception as e:
            links = {}
        for person in names:
            rec = people[person]
            try:
                if rec.get("photo") and not rec.get("photo_local"):
                    local, why = faces.process(rec["photo"], slugify(person))
                    if local:
                        out.append((person, local, rec.get("wiki") or "wikipedia", "ok"))
                        continue
                local, src, why = (None, None, "skipped") if a.no_bios else \
                    bios.find(person, domain, links)
                if not local:
                    # the firm publishes no roster: fall through to search
                    local, src, why = imagesearch.find(
                        person, rec.get("firm") or "", slugify(person), used=USED)
                out.append((person, local, src, why))
            except Exception as e:
                out.append((person, None, None, type(e).__name__))
        return out

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for group in ex.map(do_firm, sorted(by_dom.items())):
            for person, local, source, why in group:
                if local:
                    people[person]["photo_local"] = local
                    people[person]["photo_source"] = source
                    USED.add(source)
                    found[0] += 1
                    json.dump(people, open(PEOPLE, "w"), indent=2)
                print(f"{'OK ' if local else '-- '}{person:26s} "
                      f"{(source or why)[:60]}", flush=True)

    have = sum(1 for v in people.values() if v.get("photo_local"))
    print(f"\n{found[0]} new · {have}/{len(people)} principals now have a headshot")
