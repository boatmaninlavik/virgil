"""Databento price backend — licensed daily bars, batched.

Replaces the unofficial Yahoo endpoint. Two practical differences:

  * one request returns many symbols, instead of one request per symbol
  * it costs money, so every query is priced with metadata.get_cost first and
    refuses to run if the estimate exceeds a ceiling

EQUS.SUMMARY is the consolidated US equities daily summary — the right dataset
for this job and ~8x cheaper than DBEQ.BASIC for identical ohlcv-1d bars.
"""
import base64, json, os, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "prices")
CREDS = os.path.expanduser("~/.virgil/credentials")
BASE = "https://hist.databento.com/v0/"
DATASET = os.environ.get("DB_DATASET", "EQUS.SUMMARY")
SCALE = 1e-9                      # Databento prices are int64 nanos


def key():
    for line in open(CREDS):
        if line.startswith("DATABENTO_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no DATABENTO_API_KEY in ~/.virgil/credentials")


def _auth(req, k):
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{k}:".encode()).decode())


def call(path, params, raw=False, timeout=180):
    k = key()
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    _auth(req, k)
    try:
        body = urllib.request.urlopen(req, timeout=timeout).read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code}: {e.read().decode()[:200]}")
    return body if raw else json.loads(body)


def cost(symbols, start, end, dataset=DATASET, chunk=200):
    """Priced in chunks: a 1,400-symbol list overflows the request URL (414)."""
    total = 0.0
    syms = list(symbols)
    for i in range(0, len(syms), chunk):
        total += call("metadata.get_cost", {
            "dataset": dataset, "start": start, "end": end, "mode": "historical",
            "schema": "ohlcv-1d", "symbols": ",".join(syms[i:i + chunk]),
            "stype_in": "raw_symbol"})
        time.sleep(0.2)
    return total


def bars(symbols, start, end, dataset=DATASET):
    """{symbol: [[YYYY-MM-DD, close], ...]} for a batch of symbols."""
    body = call("timeseries.get_range", {
        "dataset": dataset, "start": start, "end": end, "schema": "ohlcv-1d",
        "symbols": ",".join(symbols), "stype_in": "raw_symbol",
        "encoding": "json", "map_symbols": "true"}, raw=True)
    out = {}
    for line in body.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        sym = r.get("symbol") or (r.get("hd") or {}).get("symbol")
        if not sym:
            continue
        ts = r.get("hd", {}).get("ts_event") or r.get("ts_event")
        close = r.get("close")
        if ts is None or close is None:
            continue
        day = datetime.fromtimestamp(int(ts) / 1e9, timezone.utc).strftime("%Y-%m-%d")
        out.setdefault(sym, []).append([day, round(int(close) * SCALE, 4)])
    for s in out:
        out[s].sort()
    return out


def save(sym, series, dataset=DATASET):
    os.makedirs(CACHE, exist_ok=True)
    json.dump({
        "ticker": sym, "currency": "USD", "exchange": dataset,
        "price": series[-1][1] if series else None,
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "series": series,
        "source": f"Databento {dataset} ohlcv-1d (licensed daily bars)",
    }, open(os.path.join(CACHE, f"{sym}.json"), "w"))


def run(symbols, start, end, batch=200, ceiling=5.00, dry=False):
    symbols = sorted({s.upper() for s in symbols if s and " " not in s})
    est = cost(symbols, start, end)
    print(f"{len(symbols)} symbols, {start} -> {end}, {DATASET}")
    print(f"estimated cost: ${est:.4f}")
    if est > ceiling:
        raise SystemExit(f"refusing to run: estimate ${est:.2f} exceeds ceiling ${ceiling:.2f}")
    if dry:
        return 0, est
    got = 0
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        try:
            d = bars(chunk, start, end)
        except Exception as e:
            print(f"  batch {i//batch} failed: {e}")
            continue
        for s, series in d.items():
            if series:
                save(s, series)
                got += 1
        print(f"  {min(i+batch, len(symbols))}/{len(symbols)}  saved={got}", flush=True)
        time.sleep(0.4)
    return got, est


if __name__ == "__main__":
    import argparse, glob
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--start", default="2024-09-01")
    ap.add_argument("--end", default="2026-08-28")
    ap.add_argument("--all", action="store_true", help="every ticker the page can resolve")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--ceiling", type=float, default=5.00)
    a = ap.parse_args()

    syms = a.symbols
    if a.all:
        cm = json.load(open(os.path.join(DATA, "cusip_map.json")))
        syms = {v["ticker"] for v in cm.values() if v.get("ticker")}
        ct = json.load(open(os.path.join(DATA, "congress", "by_ticker.json")))
        syms |= set(ct)
        syms |= {os.path.basename(f)[:-5] for f in glob.glob(os.path.join(CACHE, "*.json"))}
    got, est = run(syms, a.start, a.end, ceiling=a.ceiling, dry=a.dry)
    print(f"\n{got} symbols written; billed about ${est:.4f}")
