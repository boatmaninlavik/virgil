"""Live-ish quotes, free and keyless, batched.

Databento supplies the daily history but is end-of-day, so the dashboard's
"price now" was always yesterday's close. Yahoo's spark endpoint takes a
comma-separated symbol list and returns a current quote per symbol — one
request per ~50 tickers instead of one per ticker.

Not licensed real-time: the delay each exchange reports is carried through to
the page so nothing pretends to be live when it is not.
"""
import json, os, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "quotes.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
SPARK = "https://query1.finance.yahoo.com/v7/finance/spark"
BATCH = 20


def fetch(symbols):
    url = SPARK + "?" + urllib.parse.urlencode(
        {"symbols": ",".join(symbols), "range": "1d", "interval": "5m"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    d = json.loads(urllib.request.urlopen(req, timeout=30).read())
    out = {}
    for r in (d.get("spark", {}) or {}).get("result", []):
        sym = r.get("symbol")
        for resp in r.get("response", []):
            m = resp.get("meta") or {}
            px = m.get("regularMarketPrice")
            if sym and px is not None:
                out[sym] = {
                    "price": round(float(px), 4),
                    "prev_close": m.get("chartPreviousClose"),
                    "delay_s": m.get("exchangeDataDelayedBy"),
                    "exchange": m.get("fullExchangeName"),
                    "ts": m.get("regularMarketTime"),
                }
    return out


def fetch_resilient(chunk, out, bad, depth=0):
    """Yahoo rejects the whole batch if one symbol is unknown, so bisect on
    failure until the offending symbols are isolated and skipped."""
    if not chunk:
        return
    try:
        out.update(fetch(chunk))
        return
    except Exception:
        if len(chunk) == 1:
            bad.append(chunk[0])
            return
    time.sleep(0.2)
    mid = len(chunk) // 2
    fetch_resilient(chunk[:mid], out, bad, depth + 1)
    fetch_resilient(chunk[mid:], out, bad, depth + 1)


def build(symbols, verbose=True):
    symbols = sorted({s.upper() for s in symbols if s and " " not in s})
    quotes, bad = {}, []
    for i in range(0, len(symbols), BATCH):
        fetch_resilient(symbols[i:i + BATCH], quotes, bad)
        time.sleep(0.3)
        if verbose and i and i % (BATCH * 4) == 0:
            print(f"  {i}/{len(symbols)}  ok={len(quotes)} skipped={len(bad)}", flush=True)
    if verbose and bad:
        print(f"  {len(bad)} symbols Yahoo does not know: {', '.join(bad[:12])}"
              f"{'...' if len(bad) > 12 else ''}", file=sys.stderr)
    payload = {"fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "source": "Yahoo Finance spark (unlicensed; exchange delay reported per symbol)",
               "quotes": quotes}
    json.dump(payload, open(OUT, "w"), separators=(",", ":"))
    return quotes


def all_symbols():
    """Every ticker the dashboard can resolve — quotes are free, so cover them
    all rather than guessing which ones the user will look up."""
    syms = set()
    p = os.path.join(DATA, "cusip_map.json")
    if os.path.exists(p):
        syms |= {v["ticker"] for v in json.load(open(p)).values()
                 if v.get("ticker") and " " not in v["ticker"]}
    p = os.path.join(DATA, "congress", "by_ticker.json")
    if os.path.exists(p):
        syms |= set(json.load(open(p)))
    d = os.path.join(DATA, "stocks")
    if os.path.isdir(d):
        syms |= {f[:-5] for f in os.listdir(d) if f.endswith(".json")}
    return sorted(s for s in syms if s and " " not in s)


def hot_symbols(limit=400):
    """Tickers actually surfaced in the UI — no point quoting the long tail."""
    syms = set()
    sdir = os.path.join(DATA, "stocks")
    if os.path.isdir(sdir):
        syms |= {f[:-5] for f in os.listdir(sdir) if f.endswith(".json")}
    ranked = []
    for f in os.listdir(os.path.join(DATA, "holdings")):
        if not f.endswith(".json"):
            continue
        d = json.load(open(os.path.join(DATA, "holdings", f)))
        top = sorted(d["positions"].items(), key=lambda kv: -(kv[1]["value"] or 0))[:25]
        ranked += top
    cm = json.load(open(os.path.join(DATA, "cusip_map.json")))
    for cu, p in ranked:
        t = (cm.get(cu) or {}).get("ticker")
        if t:
            syms.add(t)
    p = os.path.join(DATA, "congress", "by_ticker.json")
    if os.path.exists(p):
        syms |= set(list(json.load(open(p)))[:120])
    return sorted(syms)[:limit]


if __name__ == "__main__":
    syms = sys.argv[1:] or all_symbols()
    q = build(syms)
    print(f"{len(q)}/{len(syms)} live quotes -> data/quotes.json")
    for s in list(q)[:5]:
        d = q[s]
        print(f"  {s:6s} ${d['price']:>9.2f}  delay {d['delay_s']}s  {d['exchange']}")
