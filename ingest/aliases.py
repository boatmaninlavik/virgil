"""Map a person's name to the entity that actually files for them.

Searching "cathie wood" finds nothing on EDGAR, and that is not a data gap: she
files as ARK Investment Management. Almost every investor does this - the fund
is the registrant, the person is just who runs it - so a name search that only
looks at registrant names fails on exactly the names people type.

Most of the map builds itself from the funds we already track, since each one
records its principal. The rest is a short hand-written list of people whose
firm carries a name you would never guess from theirs.

    python3 ingest/aliases.py
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "aliases.json")

# Well-known investors whose registrant name shares nothing with their own.
# Kept short and hand-checked on purpose: a wrong row here puts the wrong
# person's trades in front of someone.
CURATED = {
    "cathie wood": "ARK Investment Management",
    "catherine wood": "ARK Investment Management",
    "cathy wood": "ARK Investment Management",
    "kathy wood": "ARK Investment Management",
    "warren buffett": "Berkshire Hathaway",
    "michael burry": "Scion Asset Management",
    "david tepper": "Appaloosa",
    "seth klarman": "Baupost Group",
    "chase coleman": "Tiger Global Management",
    "philippe laffont": "Coatue Management",
    "stanley druckenmiller": "Duquesne Family Office",
    "david einhorn": "Greenlight Capital",
    "daniel loeb": "Third Point",
    "dan loeb": "Third Point",
    "bill ackman": "Pershing Square Capital Management",
    "william ackman": "Pershing Square Capital Management",
    "carl icahn": "Icahn Capital",
    "nelson peltz": "Trian Fund Management",
    "paul singer": "Elliott Investment Management",
    "ray dalio": "Bridgewater Associates",
    "steve cohen": "Point72 Asset Management",
    "steven cohen": "Point72 Asset Management",
    "israel englander": "Millennium Management",
    "izzy englander": "Millennium Management",
    "ken griffin": "Citadel Advisors",
    "kenneth griffin": "Citadel Advisors",
    "jim simons": "Renaissance Technologies",
    "cliff asness": "AQR Capital Management",
    "howard marks": "Oaktree Capital",
    "li lu": "Himalaya Capital",
    "mohnish pabrai": "Pabrai Investment",
    "guy spier": "Aquamarine",
    "terry smith": "Fundsmith",
    "nick train": "Lindsell Train",
    "chris hohn": "TCI Fund Management",
    "daniel sundheim": "D1 Capital Partners",
    "gabe plotkin": "Melvin Capital",
    "bill gross": "Pimco",
    "john paulson": "Paulson",
    "steve mandel": "Lone Pine Capital",
    "stephen mandel": "Lone Pine Capital",
    "andreas halvorsen": "Viking Global",
    "lee ainslie": "Maverick Capital",
    "larry robbins": "Glenview Capital",
    "barry rosenstein": "JANA Partners",
    "jeff ubben": "Inclusive Capital",
    "mason morfit": "ValueAct",
    "keith meister": "Corvex",
    "scott ferguson": "Sachem Head",
    "jeff smith": "Starboard Value",
    "glen kacher": "Light Street Capital",
    "brad gerstner": "Altimeter Capital",
}


def norm(s):
    return re.sub(r"[^a-z ]", "", (s or "").lower()).strip()


def build():
    filers = json.load(open(os.path.join(DATA, "filers.json")))
    by_name = {}
    for cik, rec in filers.items():
        by_name.setdefault(norm(rec.get("n")), []).append((cik, rec))

    def best(firm):
        """The filer whose name starts with this firm name and filed most recently."""
        want = norm(firm)
        cands = []
        for key, rows in by_name.items():
            if key.startswith(want) or want.startswith(key):
                cands += rows
        if not cands:
            toks = want.split()
            for key, rows in by_name.items():
                ktoks = key.split()
                if all(any(k.startswith(t) for k in ktoks) for t in toks):
                    cands += rows
        if not cands:
            return None
        cands.sort(key=lambda cr: (("13F-HR" in cr[1].get("f", [])), cr[1].get("d", "")),
                   reverse=True)
        return cands[0]

    out, missed = {}, []
    # 1. everyone we already track: the fund file records who runs it
    funds = json.load(open(os.path.join(DATA, "funds.json")))
    for label, f in funds.items():
        for who in re.split(r"\s*&\s*|,", f.get("person") or ""):
            if len(norm(who).split()) >= 2:
                out[norm(who)] = {"cik": f["cik"], "filer": f.get("edgar_name") or label,
                                  "via": "tracked"}
    # 2. the hand-written list, resolved against the filer index
    for person, firm in CURATED.items():
        hit = best(firm)
        if hit:
            out.setdefault(norm(person), {"cik": hit[0], "filer": hit[1]["n"],
                                          "via": "curated"})
        else:
            missed.append((person, firm))
    return out, missed


if __name__ == "__main__":
    out, missed = build()
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(f"{len(out)} person -> filer aliases")
    if missed:
        print(f"{len(missed)} curated firms not found in the filer index:")
        for p, f in missed:
            print(f"   {p:26s} -> {f}")
