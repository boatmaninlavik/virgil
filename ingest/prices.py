"""Price history, so a filing can be scored instead of just listed.

EDGAR has no prices — it is a filings archive. This is the only part of Virgil
that depends on a source outside sec.gov.

On "real time": the signals here lag 2 days (Form 4) to 135 days (13F), so
paying for a licensed real-time feed would buy precision the input cannot use.
Daily closes are sufficient; the quote is near-real-time as a bonus.
"""
import json, os, sys, time, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "prices")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
MAX_AGE = 6 * 3600          # refresh a symbol at most every 6h


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return json.loads(urllib.request.urlopen(req, timeout=25).read())


def fetch(ticker, rng="2y", interval="1d"):
    """Daily closes plus the latest quote. Returns None if the source fails."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?range={rng}&interval={interval}")
    try:
        r = _get(url)["chart"]["result"][0]
    except Exception as e:
        print(f"  price fetch failed for {ticker}: {e}", file=sys.stderr)
        return None
    meta = r.get("meta", {})
    stamps = r.get("timestamp") or []
    closes = (r.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    series = []
    for t, c in zip(stamps, closes):
        if c is None:
            continue
        series.append([datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"),
                       round(float(c), 4)])
    if not series:
        return None
    return {
        "ticker": ticker.upper(),
        "currency": meta.get("currency"),
        "exchange": meta.get("fullExchangeName"),
        "price": meta.get("regularMarketPrice"),
        "prev_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "series": series,
        "source": "Yahoo Finance chart API (unofficial, unlicensed — daily closes)",
    }


def _databento_available():
    p = os.path.expanduser("~/.virgil/credentials")
    return os.path.exists(p) and "DATABENTO_API_KEY=db-" in open(p).read()


def load(ticker, max_age=MAX_AGE):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, f"{ticker.upper()}.json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < max_age:
        try:
            return json.load(open(p))
        except Exception:
            pass
    # Databento is the licensed source when a key is present; Yahoo is the
    # keyless fallback so the tool still works without one.
    if _databento_available():
        try:
            import databento_prices as db
            series = db.bars([ticker.upper()], "2024-09-01", "2026-08-29").get(ticker.upper())
            if series:
                db.save(ticker.upper(), series)
                return json.load(open(p))
        except Exception as e:
            print(f"  databento failed for {ticker} ({e}); falling back to Yahoo",
                  file=sys.stderr)
    d = fetch(ticker)
    if d:
        json.dump(d, open(p, "w"))
        return d
    return json.load(open(p)) if os.path.exists(p) else None


def close_on(series, date):
    """Close on `date`, else the last close before it (handles weekends)."""
    best = None
    for d, c in series:
        if d <= date:
            best = c
        else:
            break
    return best


def score(px, date, ref_price=None):
    """Return since `date` (or since an explicit execution price)."""
    if not px or not px.get("series"):
        return None
    now = px.get("price") or px["series"][-1][1]
    base = ref_price if ref_price else close_on(px["series"], date)
    if not base:
        return None
    return {"then": round(base, 4), "now": round(now, 4),
            "change_pct": round((now - base) / base * 100, 2)}


if __name__ == "__main__":
    for t in sys.argv[1:] or ["INOD"]:
        d = load(t, max_age=0)
        if not d:
            print(f"{t}: no data")
            continue
        s = d["series"]
        print(f"{d['ticker']:6s} {d['exchange']:14s} ${d['price']:<10.2f} "
              f"{len(s)} daily closes  {s[0][0]} -> {s[-1][0]}")
