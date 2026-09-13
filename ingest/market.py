"""Market-wide Form 4 scan for insider cluster buys.

The watchlist poller only sees 48 entities. Cluster buying — several distinct
insiders of the same company buying on the open market within days of each
other — is the strongest documented signal in this data, and it only shows up
if you look at every Form 4, not a curated list. EDGAR's daily index makes
that tractable: ~1,200 filings a day, one request each.
"""
import json, os, sys, time
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "market_f4.json")

MIN_INSIDERS = 3        # distinct people
MIN_VALUE = 25_000      # ignore token purchases


def business_days(n, end=None):
    d = end or date.today()
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return out


def load_cache():
    return json.load(open(CACHE)) if os.path.exists(CACHE) else {}


def scan_day(day, cache, verbose=True):
    rows = [r for r in edgar.daily_index(day) if r["form"] == "4"]
    todo = [r for r in rows if r["path"] not in cache]
    if verbose:
        print(f"  {day}: {len(rows):4d} Form 4  ({len(todo)} new)", flush=True)
    if not todo:
        return 0

    errs = []
    bodies = edgar.fetch_many([r["url"] for r in todo],
                              on_error=lambda u, e: errs.append(str(e)))
    added = 0
    for r in todo:
        body = bodies.get(r["url"])
        xml = edgar.extract_ownership(body)
        if not xml:
            cache[r["path"]] = None          # remember the miss; don't refetch
            continue
        rec = edgar.parse_form4(xml, r["url"])
        if not rec:
            cache[r["path"]] = None
            continue
        buys = [t for t in rec["transactions"]
                if t["code"] == "P" and t["shares"] and t["table"] == "I"]
        if not buys:
            cache[r["path"]] = None
            continue
        owner = next((o for o in rec["owners"]), {}) or {}
        cache[r["path"]] = {
            "filed": day,
            "issuer_cik": rec["issuer_cik"],
            "issuer": rec["issuer_name"],
            "ticker": rec["ticker"],
            "owner_cik": owner.get("cik", ""),
            "owner": owner.get("name", ""),
            "roles": owner.get("roles", []),
            "shares": sum(t["shares"] or 0 for t in buys),
            "value": sum((t["value"] or 0) for t in buys),
            "txn_date": buys[0]["date"],
            "url": r["url"].replace(".txt", "-index.htm"),
        }
        added += 1
    if errs and verbose:
        print(f"      {len(errs)} fetch errors", flush=True)
    return added


def find_clusters(cache, days_window):
    """Group open-market buys by issuer; flag several distinct insiders."""
    by_issuer = defaultdict(list)
    for v in cache.values():
        if v and v["filed"] in days_window:
            by_issuer[v["issuer_cik"]].append(v)

    clusters = []
    for cik, buys in by_issuer.items():
        people = {b["owner_cik"] for b in buys if b["owner_cik"]}
        total = sum(b["value"] or 0 for b in buys)
        if len(people) >= MIN_INSIDERS and total >= MIN_VALUE:
            buys.sort(key=lambda b: b["filed"], reverse=True)
            # one person filing twice is still one buyer
            agg = {}
            for b in buys:
                k = b["owner_cik"] or b["owner"]
                a = agg.setdefault(k, {"name": b["owner"], "roles": b["roles"],
                                       "value": 0.0, "filed": b["filed"],
                                       "filings": 0})
                a["value"] += b["value"] or 0
                a["filings"] += 1
                a["filed"] = min(a["filed"], b["filed"])
                if not a["roles"]:
                    a["roles"] = b["roles"]
            buyers = sorted(agg.values(), key=lambda a: -a["value"])
            clusters.append({
                "issuer_cik": cik,
                "issuer": buys[0]["issuer"],
                "ticker": buys[0]["ticker"],
                "insiders": len(people),
                "filings": len(buys),
                "value": total,
                "shares": sum(b["shares"] or 0 for b in buys),
                "first_filed": min(b["filed"] for b in buys),
                "last_filed": max(b["filed"] for b in buys),
                "buyers": buyers,
                "url": buys[0]["url"],
            })
    clusters.sort(key=lambda c: (-c["insiders"], -c["value"]))
    return clusters


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    a = ap.parse_args()

    t0 = time.time()
    cache = load_cache()
    days = business_days(a.days)
    print(f"scanning {len(days)} business days: {days[-1]} -> {days[0]}")
    total = 0
    for d in reversed(days):
        total += scan_day(d, cache)
        json.dump(cache, open(CACHE, "w"))     # checkpoint after each day

    clusters = find_clusters(cache, set(days))
    json.dump(clusters, open(os.path.join(DATA, "clusters.json"), "w"), indent=1)

    buys = sum(1 for v in cache.values() if v)
    print(f"\n{total} new open-market buys parsed ({buys} cached total) "
          f"in {time.time()-t0:.0f}s")
    print(f"{len(clusters)} cluster buys (>={MIN_INSIDERS} distinct insiders)\n")
    for c in clusters[:14]:
        print(f"  {(c['ticker'] or '—'):6s} {c['insiders']} insiders  "
              f"${c['value']:>12,.0f}  {c['issuer'][:38]:40s} {c['first_filed']}")
