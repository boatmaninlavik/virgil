"""CUSIP -> ticker for 13F positions.

13F information tables carry a CUSIP and a hand-typed issuer name, never a
ticker, so nothing downstream can look up a price without this step. OpenFIGI
is free and needs no key at low volume (25 requests/min, 10 jobs per request).
"""
import json, os, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
MAP = os.path.join(DATA, "cusip_map.json")
API = "https://api.openfigi.com/v3/mapping"
BATCH, PER_MIN = 10, 20          # stay under the unauthenticated limit


def load_map():
    return json.load(open(MAP)) if os.path.exists(MAP) else {}


def wanted(top_n=0):
    import glob
    out = set()
    for f in glob.glob(os.path.join(DATA, "holdings", "*.json")):
        d = json.load(open(f))
        rows = sorted(d["positions"].items(), key=lambda kv: -(kv[1]["value"] or 0))
        out |= {cu for cu, _ in (rows[:top_n] if top_n else rows)}
    return {c for c in out if c and len(c) == 9}


def resolve(cusips, m, verbose=True):
    todo = [c for c in sorted(cusips) if c not in m]
    if verbose:
        print(f"{len(todo)} unmapped CUSIPs ({len(m)} already cached)")
    gap = 60.0 / PER_MIN
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        body = [{"idType": "ID_CUSIP", "idValue": c} for c in chunk]
        req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            res = json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception as e:
            if verbose:
                print(f"  batch {i//BATCH} failed: {e}")
            time.sleep(5)
            continue
        for c, r in zip(chunk, res):
            d = (r.get("data") or [{}])[0]
            t = d.get("ticker")
            # FIGI returns bond descriptors ("LI 0.25 05-01-28") for converts;
            # those are not equity symbols and have no price series
            if t and (" " in t or d.get("securityType2") == "Corp"):
                t = None
            # 13F reports US listings; FIGI writes class shares as BRK/B
            m[c] = {"ticker": t.replace("/", "-") if t else None,
                    "name": d.get("name"), "exch": d.get("exchCode")}
        if i % (BATCH * 20) == 0:
            json.dump(m, open(MAP, "w"))
            if verbose:
                print(f"  {i+len(chunk)}/{len(todo)}", flush=True)
        time.sleep(gap)
    json.dump(m, open(MAP, "w"))
    return m


if __name__ == "__main__":
    m = load_map()
    m = resolve(wanted(), m)
    hit = sum(1 for v in m.values() if v.get("ticker"))
    print(f"\n{hit}/{len(m)} CUSIPs mapped to a ticker")
