"""Market-wide Form 4 activity indexed by issuer.

market.py scans every Form 4 but keeps only open-market *buys*, because it
exists to find cluster buying. The per-stock view needs both sides, and needs
them keyed by ticker rather than by the fund watchlist.

This is the fast lane: Form 4 carries an exact trade date and execution price
and lands a median of 2 days later, against 45+ for a 13F that only tells you
a position moved sometime inside a quarter.
"""
import glob, json, os, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar
import store
from market import business_days

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "insider_f4.json")
OUT = os.path.join(DATA, "insiders_by_ticker.json")
KEEP = {"P", "S"}                      # open-market intent only
BLANK = {"", "N/A", "NONE", "NA", "N.A."}   # issuer has no listed symbol
LIVE = os.path.join(DATA, "live_f4.json")   # intraday lane, see live_f4.py


def accession_of(url):
    """The accession number identifies a filing; the CIK in its path does not.

    EDGAR's daily index lists a Form 4 once per filer, and both the issuer and
    the reporting owner are filers — so the same document arrives twice under
    two CIK paths. Keying on the path double-counted every insider trade.
    """
    return (url or "").rsplit("/", 1)[-1].replace("-index.htm", "").replace(".txt", "")


def scan_day(day, cache, verbose=True):
    rows = [r for r in edgar.daily_index(day) if r["form"] == "4"]
    todo = [r for r in rows if r["path"] not in cache]
    if verbose:
        print(f"  {day}: {len(rows):5d} Form 4 ({len(todo)} new)", flush=True)
    if not todo:
        return 0
    bodies = edgar.fetch_many([r["url"] for r in todo], on_error=lambda u, e: None)
    added = 0
    for r in todo:
        xml = edgar.extract_ownership(bodies.get(r["url"]))
        rec = edgar.parse_form4(xml, r["url"]) if xml else None
        if not rec:
            cache[r["path"]] = None
            continue
        txns = [t for t in rec["transactions"]
                if t["code"] in KEEP and t["shares"] and t["table"] == "I"]
        if not txns or rec.get("ticker", "").upper() in BLANK:
            cache[r["path"]] = None
            continue
        owner = next((o for o in rec["owners"]), {}) or {}
        cache[r["path"]] = {
            "filed": day, "ticker": rec["ticker"].upper(),
            "issuer": rec["issuer_name"], "issuer_cik": rec["issuer_cik"],
            "who": owner.get("name", ""), "who_cik": owner.get("cik", ""),
            "roles": owner.get("roles", []),
            "rule_10b5_1": rec["rule_10b5_1"],
            "t": [[t["date"], t["code"], t["shares"], t["price"], t["value"]]
                  for t in txns],
            "url": r["url"].replace(".txt", "-index.htm"),
        }
        added += 1
    return added


def build_index(cache):
    idx, seen = defaultdict(list), set()
    for v in cache.values():
        if not v:
            continue
        acc = accession_of(v["url"])
        if acc in seen:
            continue                   # same filing, other party's CIK path
        seen.add(acc)
        for d, code, sh, px, val in v["t"]:
            idx[v["ticker"]].append({
                "who": v["who"], "roles": v["roles"], "issuer": v["issuer"],
                "action": "buy" if code == "P" else "sell",
                "date": d, "filed": v["filed"], "shares": sh,
                "price": px, "value": val, "rule_10b5_1": v["rule_10b5_1"],
                "url": v["url"],
            })
    for t in idx:
        idx[t].sort(key=lambda r: (r["date"] or "", r["filed"]), reverse=True)
        idx[t] = idx[t][:40]
    return dict(idx)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    a = ap.parse_args()
    # The per-filing cache is the thing that fills the disk: ~1,400 entries a
    # trading day, past 100 MB over a 20-day window, and read by nothing but
    # this scanner. Keep it in the bucket; keep only the derived index locally.
    cache = store.get_json("insider_f4.json", None)
    if cache is None:
        cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    print(f"cache: {len(cache)} filings (held in GCS, not on disk)")
    t0 = time.time()
    days = business_days(a.days)
    print(f"scanning {len(days)} business days: {days[-1]} -> {days[0]}")
    # Newest first: an interrupted scan should leave you with current data,
    # not three-week-old data. Running oldest-first meant a pause left the feed
    # showing early-August trades in September.
    for d in days:
        scan_day(d, cache)
        store.put_json("insider_f4.json", cache)
        idx = build_index(cache)
        json.dump(idx, open(OUT, "w"), separators=(",", ":"))
    # The intraday lane sees filings hours before they reach the daily index.
    # Fold it in so a rebuild never rolls the page back to yesterday's close.
    if os.path.exists(LIVE):
        live = json.load(open(LIVE))
        cache = {**live, **cache}
        print(f"merged {sum(1 for v in live.values() if v)} live filings")
    idx = build_index(cache)
    json.dump(idx, open(OUT, "w"), separators=(",", ":"))
    if os.path.exists(CACHE):
        os.remove(CACHE)            # superseded by the copy in GCS
    n = sum(len(v) for v in idx.values())
    print(f"\n{len(idx)} tickers, {n} insider transactions, {time.time()-t0:.0f}s")
