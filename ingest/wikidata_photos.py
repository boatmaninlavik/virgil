"""Wikidata P18 portraits.

photos.py had a wikidata_image() stub that always returned None, so this source
was never actually queried. P18 holds a Commons portrait for many people whose
English Wikipedia article shows no lead image.
"""
import json, os, sys, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "sean@erised.me virgil/0.1"}


def get(url):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=25).read())


def search_entity(name, hint=""):
    q = urllib.parse.urlencode({"action": "wbsearchentities", "search": name,
                                "language": "en", "format": "json", "limit": "8",
                                "type": "item"})
    d = get("https://www.wikidata.org/w/api.php?" + q)
    out = []
    for r in d.get("search", []):
        desc = (r.get("description") or "").lower()
        # only people, and preferably finance people
        if any(k in desc for k in ("businessman", "investor", "hedge fund", "financier",
                                   "manager", "executive", "businessperson", "economist",
                                   "entrepreneur", "philanthropist")):
            out.append((r["id"], r.get("label"), r.get("description")))
    return out


def p18(qid):
    d = get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
    ent = d["entities"][qid]
    claims = ent.get("claims", {}).get("P18") or []
    if not claims:
        return None
    fn = claims[0]["mainsnak"]["datavalue"]["value"]
    return ("https://commons.wikimedia.org/wiki/Special:FilePath/"
            + urllib.parse.quote(fn.replace(" ", "_")) + "?width=512")


if __name__ == "__main__":
    people = json.load(open(os.path.join(DATA, "people.json")))
    todo = [(k, v) for k, v in people.items() if not v.get("photo")]
    print(f"{len(todo)} principals still without a portrait")
    found = 0
    for person, p in todo:
        try:
            hits = search_entity(person)
        except Exception:
            continue
        for qid, label, desc in hits[:3]:
            # surname must match, so we do not attach a stranger's face
            if person.split()[-1].lower() not in (label or "").lower():
                continue
            try:
                url = p18(qid)
            except Exception:
                continue
            if url:
                p["photo"] = url
                p["source"] = f"Wikidata {qid} ({desc})"
                p.pop("shape", None)
                found += 1
                print(f"  {person:24s} {qid}  {desc[:46]}")
                break
        json.dump(people, open(os.path.join(DATA, "people.json"), "w"), indent=2)
    print(f"\n{found} recovered from Wikidata; "
          f"{sum(1 for v in people.values() if v.get('photo'))}/{len(people)} have a portrait")
