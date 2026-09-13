"""Multi-quarter 13F history, so a manager's book has a time series.

holdings.py keeps only the latest quarter and the one before it, which is
enough to label a position added/trimmed but gives a two-point line. This
walks back further and records reported portfolio value per quarter, plus the
position count, so the UI can chart how a manager's book actually moved.

Reported value is the 13F total: long US equity positions at quarter-end. It is
not AUM and not performance — a fund can be flat while the number swings on
inflows or a single re-marked holding.
"""
import json, os, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar
from holdings import infotable_url

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "history")
QUARTERS = int(os.environ.get("QUARTERS", "10"))


def fund_history(cik, label, quarters=QUARTERS, verbose=True):
    cache_p = os.path.join(OUT, f"{cik}.json")
    cached = json.load(open(cache_p)) if os.path.exists(cache_p) else {"periods": {}}
    try:
        fils = edgar.recent_filings(cik, forms={"13F-HR"}, limit=quarters + 4)
    except Exception:
        return cached

    seen = set()
    for f in fils:
        period = f["period"]
        if not period or period in cached["periods"] or period in seen:
            continue
        seen.add(period)
        if len(cached["periods"]) + len(seen) > quarters + 4:
            break
        try:
            u = infotable_url(cik, f["accession"])
            rows = edgar.parse_13f(edgar.fetch(u)) if u else []
        except Exception:
            continue
        if not rows:
            continue
        total = sum(r["value"] for r in rows if not r.get("put_call"))
        top = sorted((r for r in rows if not r.get("put_call")),
                     key=lambda r: -r["value"])[:10]
        cached["periods"][period] = {
            "filed": f["filed"],
            "value": total,
            "positions": len({r["cusip"] for r in rows if not r.get("put_call")}),
            "top": [{"issuer": r["issuer"], "cusip": r["cusip"],
                     "value": r["value"], "shares": r["shares"]} for r in top],
        }
    cached["cik"] = cik
    cached["fund"] = label
    os.makedirs(OUT, exist_ok=True)
    json.dump(cached, open(cache_p, "w"), separators=(",", ":"))
    return cached


if __name__ == "__main__":
    funds = json.load(open(os.path.join(DATA, "funds.json")))
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    index = {}
    for i, (name, v) in enumerate(sorted(funds.items()), 1):
        h = fund_history(v["cik"], v["display"])
        pers = sorted(h.get("periods", {}))
        if pers:
            index[v["display"]] = {
                "cik": v["cik"], "person": v["person"],
                "series": [[p, round(h["periods"][p]["value"]),
                            h["periods"][p]["positions"]] for p in pers],
            }
        if i % 20 == 0:
            print(f"  {i}/{len(funds)}  {time.time()-t0:.0f}s", flush=True)
    json.dump(index, open(os.path.join(DATA, "fund_history.json"), "w"),
              separators=(",", ":"))
    depth = [len(v["series"]) for v in index.values()]
    print(f"\n{len(index)} funds with history; median {sorted(depth)[len(depth)//2]} "
          f"quarters, max {max(depth)}, in {time.time()-t0:.0f}s")
