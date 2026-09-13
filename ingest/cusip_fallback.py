"""Second pass for CUSIPs OpenFIGI cannot resolve.

Foreign issuers appear in 13F tables under CINS codes (letter-prefixed:
Credo is Cayman "G25457105", ASML is Dutch "N07059210"). OpenFIGI's ID_CUSIP
lookup returns "No identifier found" for those, which silently drops some of
the largest positions in a fund. SEC's own company_tickers.json covers them,
so match the 13F issuer name against it using the same tested matcher the
per-ticker lookup uses.
"""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar
from stock import norm_name, tok_match

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
MAP = os.path.join(DATA, "cusip_map.json")

if __name__ == "__main__":
    cmap = json.load(open(MAP))
    unresolved = {c for c, v in cmap.items() if not (v or {}).get("ticker")}
    print(f"{len(unresolved)} CUSIPs unresolved by OpenFIGI")

    # issuer name as it appears in the 13F tables
    names = {}
    for f in glob.glob(os.path.join(DATA, "holdings", "*.json")):
        for cu, p in json.load(open(f))["positions"].items():
            if cu in unresolved and p.get("issuer"):
                names.setdefault(cu, p["issuer"])

    sec = json.loads(edgar.fetch("https://www.sec.gov/files/company_tickers.json"))
    index = []
    for v in sec.values():
        index.append((norm_name(v["title"]), v["ticker"], v["title"]))

    fixed = 0
    for cu, issuer in names.items():
        want = norm_name(issuer)
        if not want:
            continue
        hit = next(((t, title) for n, t, title in index if tok_match(n, want)), None)
        if hit:
            cmap[cu] = {"ticker": hit[0], "name": hit[1], "exch": "SEC",
                        "via": "name-match"}
            fixed += 1
            if fixed <= 15:
                print(f"  {cu}  {issuer[:32]:34s} -> {hit[0]:6s} {hit[1][:34]}")
    json.dump(cmap, open(MAP, "w"))
    hit = sum(1 for v in cmap.values() if v.get("ticker"))
    print(f"\nrecovered {fixed}; {hit}/{len(cmap)} CUSIPs now have a ticker")
