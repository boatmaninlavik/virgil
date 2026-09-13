"""Incremental daily price update, with a hard spend ceiling.

Backfilling two years costs ~$0.93. Fetching the *latest* bar for the same
universe costs $0.0019 — roughly $0.49/yr at one refresh per trading day.
The expensive thing is re-downloading history, not staying current, so this
only ever asks for days it does not already have.

Every run appends the billed estimate to data/spend.log, and refuses to run if
the rolling 30-day total would exceed MONTHLY_CEILING.
"""
import glob, json, os, sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import databento_prices as db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SPEND = os.path.join(DATA, "spend.log")
MONTHLY_CEILING = float(os.environ.get("VIRGIL_MONTHLY_CEILING", "2.00"))


def spent_last_30d():
    if not os.path.exists(SPEND):
        return 0.0
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    tot = 0.0
    for line in open(SPEND):
        try:
            d, amt = line.split()[0], float(line.split()[-1].lstrip("$"))
        except (ValueError, IndexError):
            continue
        if d >= cutoff:
            tot += amt
    return tot


def newest_cached():
    """Latest bar date across the Databento-sourced files.

    A handful of leftover Yahoo files can carry a later date than Databento
    publishes, which would make an incremental run think it is already current
    and quietly stop updating.
    """
    dates = []
    for f in glob.glob(os.path.join(DATA, "prices", "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if "Databento" not in (d.get("source") or ""):
            continue
        s = d.get("series") or []
        if s:
            dates.append(s[-1][0])
    if not dates:
        return None
    dates.sort()
    return dates[len(dates) // 2]          # median: robust to a few stragglers


def universe():
    cm = json.load(open(os.path.join(DATA, "cusip_map.json")))
    syms = {v["ticker"] for v in cm.values() if v.get("ticker") and " " not in v["ticker"]}
    p = os.path.join(DATA, "congress", "by_ticker.json")
    if os.path.exists(p):
        syms |= set(json.load(open(p)))
    syms |= {os.path.basename(f)[:-5] for f in glob.glob(os.path.join(DATA, "prices", "*.json"))}
    return sorted(s for s in syms if s and " " not in s)


def merge(sym, new_series):
    p = os.path.join(DATA, "prices", f"{sym}.json")
    if not os.path.exists(p):
        db.save(sym, new_series)
        return
    d = json.load(open(p))
    have = {r[0] for r in d["series"]}
    d["series"] += [r for r in new_series if r[0] not in have]
    d["series"].sort()
    d["price"] = d["series"][-1][1]
    d["source"] = f"Databento {db.DATASET} ohlcv-1d (licensed daily bars)"
    json.dump(d, open(p, "w"))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default=None, help="last bar date to request")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    syms = universe()
    start = newest_cached() or (date.today() - timedelta(days=7)).isoformat()
    end = a.end or (date.today() - timedelta(days=2)).isoformat()
    if start >= end:
        print(f"already current through {start}; nothing to fetch")
        raise SystemExit(0)

    est = db.cost(syms, start, end)
    used = spent_last_30d()
    print(f"{len(syms)} symbols, {start} -> {end}: estimate ${est:.4f} "
          f"(30-day spend ${used:.2f} of ${MONTHLY_CEILING:.2f})")
    if used + est > MONTHLY_CEILING:
        raise SystemExit("ceiling reached — refusing to spend more this month")
    if a.dry:
        raise SystemExit(0)

    got = 0
    for i in range(0, len(syms), 200):
        chunk = syms[i:i + 200]
        try:
            for s, series in db.bars(chunk, start, end).items():
                if series:
                    merge(s, series)
                    got += 1
        except Exception as e:
            print(f"  batch {i//200} failed: {e}")
    with open(SPEND, "a") as fh:
        fh.write(f"{date.today().isoformat()} prices {len(syms)}sym ${est:.4f}\n")
    print(f"updated {got} symbols; billed about ${est:.4f}")
