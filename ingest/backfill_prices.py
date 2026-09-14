"""Give every ticker enough history for its own chart controls to mean something.

Most price files hold ten bars, because that is the window the incremental
update happened to fetch when the symbol first appeared. A chart built on ten
points renders identically whether the reader asks for one month or one year,
which makes the range buttons furniture.

Yahoo's spark endpoint returns two years of split-adjusted closes, batched and
keyless — the same series splits.py already measures against, so backfilling
from it keeps one definition of "the close" across the whole dataset rather
than stitching two sources into one line.

    python3 ingest/backfill_prices.py --min-bars 200
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from splits import adjusted, PRICES

MIN_BARS = 200


def short_tickers(min_bars):
    out = []
    for f in sorted(os.listdir(PRICES)):
        if not f.endswith(".json"):
            continue
        try:
            n = len(json.load(open(os.path.join(PRICES, f))).get("series") or [])
        except Exception:
            n = 0
        if n < min_bars:
            out.append(f[:-5])
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-bars", type=int, default=MIN_BARS)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    todo = short_tickers(a.min_bars)
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(todo)} tickers with fewer than {a.min_bars} bars", flush=True)

    filled = thin = 0
    CHUNK = 300          # adjusted() batches 15 per request internally
    for i in range(0, len(todo), CHUNK):
        batch = todo[i:i + CHUNK]
        ref = adjusted(batch)
        for t in batch:
            series = ref.get(t) or {}
            if len(series) < a.min_bars:
                thin += 1
                continue
            path = os.path.join(PRICES, f"{t}.json")
            try:
                doc = json.load(open(path))
            except Exception:
                doc = {"ticker": t, "currency": "USD"}
            rows = [[d, round(series[d], 4)] for d in sorted(series)]
            doc["series"] = rows
            doc["price"] = rows[-1][1]
            doc["source"] = "Yahoo adjusted close (split-adjusted)"
            doc["split_adjusted"] = True
            json.dump(doc, open(path, "w"), separators=(",", ":"))
            filled += 1
        print(f"  {min(i + CHUNK, len(todo)):5d}/{len(todo)}  filled={filled} "
              f"thin={thin}", flush=True)
        time.sleep(0.4)
    print(f"\n{filled} backfilled · {thin} had no usable history")
