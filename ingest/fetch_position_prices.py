"""Fetch price history for the largest 13F positions, so the hover card can
show what a stock was doing during the quarter a fund changed its position.

Bounded on purpose: the top N per fund, deduped. Every symbol is one request
to an unofficial endpoint, so this stays a few hundred, not thousands.
"""
import glob, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prices

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

TOP_N = int(os.environ.get("TOP_N", "15"))

cmap = json.load(open(os.path.join(DATA, "cusip_map.json")))
want = {}
for f in glob.glob(os.path.join(DATA, "holdings", "*.json")):
    d = json.load(open(f))
    rows = sorted(d["positions"].items(), key=lambda kv: -(kv[1]["value"] or 0))
    for cu, p in rows[:TOP_N]:
        t = (cmap.get(cu) or {}).get("ticker")
        if t and " " not in t:
            want[t] = max(want.get(t, 0), p.get("value") or 0)

syms = [t for t, _ in sorted(want.items(), key=lambda kv: -kv[1])]
print(f"{len(syms)} unique tickers across the top {TOP_N} positions of each fund")

ok = fail = 0
for i, t in enumerate(syms, 1):
    d = prices.load(t, max_age=20 * 3600)
    if d:
        ok += 1
    else:
        fail += 1
    if i % 50 == 0:
        print(f"  {i}/{len(syms)}  ok={ok} fail={fail}", flush=True)
    time.sleep(0.35)
print(f"\n{ok} price series cached, {fail} failed")
