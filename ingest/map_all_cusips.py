"""Map every held CUSIP to a ticker, cheaply.

cusips.py only ever mapped each fund's top 60 positions, so of 14,396 securities
held across the tracked funds only ~1,900 were searchable — Evertec was in the
data the whole time, just unreachable.

Doing the remaining ~12,500 through OpenFIGI at 200/min would take an hour. SEC
publishes company_tickers.json (~10k US issuers) and 13F issuer names are close
to the registrant names in it, so this matches offline first — instant, no API —
and leaves only the residue for OpenFIGI.

Matching is indexed rather than scanned: exact normalised name is a dict hit,
and the fuzzy path only compares against names sharing a first token, which
turns 12,500 x 10,000 comparisons into something that finishes in seconds.
"""
import glob, json, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar
from stock import norm_name, tok_match

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
MAP = os.path.join(DATA, "cusip_map.json")


def held_cusips():
    out = {}
    for f in glob.glob(os.path.join(DATA, "holdings", "*.json")):
        for cu, p in json.load(open(f))["positions"].items():
            if cu not in out and p.get("issuer"):
                out[cu] = p["issuer"]
    return out


if __name__ == "__main__":
    cmap = json.load(open(MAP)) if os.path.exists(MAP) else {}
    held = held_cusips()
    todo = {cu: name for cu, name in held.items()
            if not (cmap.get(cu) or {}).get("ticker")}
    print(f"{len(held)} CUSIPs held, {len(todo)} without a ticker")

    sec = json.loads(edgar.fetch("https://www.sec.gov/files/company_tickers.json"))
    exact, by_first = {}, defaultdict(list)
    for v in sec.values():
        n = norm_name(v["title"])
        if not n:
            continue
        exact.setdefault(n, (v["ticker"], v["title"]))
        by_first[n.split()[0]].append((n, v["ticker"], v["title"]))
    print(f"SEC registry: {len(exact)} distinct normalised names")

    hits = 0
    for cu, issuer in todo.items():
        want = norm_name(issuer)
        if not want:
            continue
        got = exact.get(want)
        if not got:
            for n, t, title in by_first.get(want.split()[0], ()):
                if tok_match(n, want):
                    got = (t, title)
                    break
        if got:
            cmap[cu] = {"ticker": got[0], "name": got[1], "exch": "SEC",
                        "via": "sec-name-match"}
            hits += 1
    json.dump(cmap, open(MAP, "w"), separators=(",", ":"))
    total = sum(1 for v in cmap.values() if v.get("ticker"))
    print(f"\nmatched {hits} offline; {total} CUSIPs now carry a ticker "
          f"({len(held) - total} still unmapped)")
