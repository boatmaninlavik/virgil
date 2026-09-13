"""Back-adjust stored price series for stock splits.

Databento's EQUS.SUMMARY daily bars are raw prints: the tape said CVNA was $340
on 2026-05-07 and $68 on 2026-05-08, because five-for-one is what the exchange
quoted. Charting that unadjusted drew an 80% overnight crash that never
happened, on 91 tickers.

Rather than look up split events, compare against a series that is already
adjusted. Yahoo's spark endpoint returns adjusted closes, batches symbols, and
- unlike its v8/chart endpoint, which 429s hard - stays available. The ratio
between the two series is the split factor, measured rather than guessed, so a
genuine one-day collapse is never mistaken for a corporate action.

    python3 ingest/splits.py             # every ticker showing a jump
    python3 ingest/splits.py CVNA ANET   # named tickers, checked regardless
"""
import json, glob, os, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES = os.path.join(ROOT, "data", "prices")
# Yahoo fingerprints the agent: a full Chrome string 429s where the bare one
# is served. live_quotes.py hits the same endpoint with the same header.
UA = {"User-Agent": "Mozilla/5.0"}
SPARK = ("https://query{}.finance.yahoo.com/v7/finance/spark"
         "?symbols={}&range=2y&interval=1d")
BATCH = 15
# Ratios a split can plausibly produce, forward or reverse.
SPLITISH = (1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50)


def adjusted(symbols, tries=3):
    """{ticker: {iso_date: adjusted_close}} from Yahoo, batched."""
    out = {}
    for i in range(0, len(symbols), BATCH):
        chunk = symbols[i:i + BATCH]
        url = SPARK.format(1 + (i // BATCH) % 2, urllib.parse.quote(",".join(chunk)))
        for n in range(tries):
            try:
                raw = urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=25).read()
                res = json.loads(raw)["spark"]["result"]
            except Exception:
                time.sleep(1.2 * (n + 1))
                continue
            for r in res:
                try:
                    body = r["response"][0]
                    ts = body["timestamp"]
                    cl = body["indicators"]["quote"][0]["close"]
                except Exception:
                    continue
                series = {}
                for t, c in zip(ts, cl):
                    if c:
                        series[time.strftime("%Y-%m-%d", time.gmtime(t))] = c
                out[r["symbol"]] = series
            break
        time.sleep(0.35)
    return out


def snap(ratio):
    """Nearest plausible split ratio, or 1.0 when the gap is just price drift."""
    if abs(ratio - 1.0) < 0.06:
        return 1.0
    for n in SPLITISH:
        if abs(ratio - n) < 0.09 * n:
            return float(n)
        if abs(ratio - 1.0 / n) < 0.09 / n:
            return 1.0 / n
    return None                       # a real move, not a corporate action


def rebuild(series, ref):
    """Replace the stored bars with the adjusted reference over its range.

    Rescaling in place looked simpler but is not safe: the reference only
    reaches back two years, so bars older than that kept their pre-split values
    and the correction introduced a fresh cliff on the day coverage began.
    Taking the reference wholesale cannot do that. Bars older than the
    reference are dropped rather than guessed - a handful of days at the far
    edge of the window, against a chart that is otherwise wrong by 5x.
    """
    if not ref:
        return 0, series
    out = [[d, round(ref[d], 4)] for d in sorted(ref) if d <= series[-1][0]]
    # Thinly traded names - warrants especially - have almost no history on the
    # reference, and swapping 499 bars for 1 is a worse chart than an
    # unadjusted one. Leave those alone and say so.
    if len(out) < 0.6 * len(series):
        return 0, series
    return len(out), out


def has_jump(series):
    """A split-shaped discontinuity inside our own series."""
    for i in range(1, len(series)):
        a, b = series[i - 1][1], series[i][1]
        if a and b and snap(a / b) not in (None, 1.0):
            return True
    return False


def needs_rebuild(series, ref):
    """Disagrees with the adjusted series, or is discontinuous on its own.

    Both tests are needed. Disagreement alone misses a series that was already
    partly corrected, which is continuous with the reference over the overlap
    yet still has a cliff where the reference's two-year window begins.
    """
    gaps = 0
    for d, v in series:
        t = ref.get(d)
        if t and v:
            f = snap(v / t)
            if f is not None and f != 1.0:
                gaps += 1
    return gaps >= 3 or has_jump(series)


def suspects(paths):
    """Tickers whose stored series has a split-shaped overnight jump.

    Only these need checking: with no discontinuity in the window there is
    nothing in it to adjust. Turns 1,263 lookups into about 90.
    """
    out = []
    for fp in paths:
        try:
            s = json.load(open(fp)).get("series") or []
        except Exception:
            continue
        for i in range(1, len(s)):
            a, b = s[i - 1][1], s[i][1]
            if a and b and snap(a / b) not in (None, 1.0):
                out.append(fp)
                break
    return out


if __name__ == "__main__":
    args = [a.upper() for a in sys.argv[1:] if not a.startswith("-")]
    allp = sorted(glob.glob(os.path.join(PRICES, "*.json")))
    paths = ([os.path.join(PRICES, f"{a}.json") for a in args] if args
             else suspects(allp))
    paths = [p for p in paths if os.path.exists(p)]
    tickers = [os.path.basename(p)[:-5] for p in paths]
    print(f"{len(allp)} tickers held; {len(tickers)} to check", flush=True)

    ref = adjusted(tickers)
    print(f"reference series fetched for {len(ref)}", flush=True)

    fixed = clean = missing = thin = 0
    for p, t in zip(paths, tickers):
        if t not in ref or not ref[t]:
            missing += 1
            continue
        doc = json.load(open(p))
        series = doc.get("series") or []
        if not needs_rebuild(series, ref[t]):
            clean += 1
            continue
        n, newseries = rebuild(series, ref[t])
        if not n:
            thin += 1
            print(f"  {t:7s} skipped - reference covers too little history",
                  flush=True)
            continue
        doc["series"] = newseries
        doc["split_adjusted"] = True
        doc["source"] = "Yahoo adjusted close (split-adjusted; Databento bars are raw)"
        if newseries:
            doc["price"] = newseries[-1][1]
        json.dump(doc, open(p, "w"), separators=(",", ":"))
        print(f"  {t:7s} {len(series):4d} -> {n:4d} bars, "
              f"last {newseries[-1][1]}", flush=True)
        fixed += 1
    print(f"\n{fixed} adjusted · {clean} already correct · {thin} too thin "
          f"to adjust safely · {missing} no reference")
