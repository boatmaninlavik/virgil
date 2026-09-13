"""Per-ticker view: who has been buying and selling one company.

Answers a different question from the watchlist feed. The feed asks "what did
these 48 entities do?"; this asks "what happened to THIS stock?" — which means
pulling every Section 16 insider of the issuer, not a curated list.

    python3 ingest/stock.py INOD
"""
import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar
import prices as px_mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
STOCKS = os.path.join(DATA, "stocks")

_tickers = None


def resolve(ticker):
    """ticker -> (cik, name) using SEC's official map."""
    global _tickers
    if _tickers is None:
        raw = json.loads(edgar.fetch("https://www.sec.gov/files/company_tickers.json"))
        _tickers = {v["ticker"].upper(): (str(v["cik_str"]).zfill(10), v["title"])
                    for v in raw.values()}
    return _tickers.get(ticker.upper())


STOP = {"inc", "inc.", "corp", "corp.", "corporation", "co", "co.", "company",
        "ltd", "ltd.", "plc", "holdings", "holding", "group", "the", "sa", "nv",
        "class", "com", "new", "&"}


def norm_name(s):
    toks = [t for t in re.sub(r"[^A-Za-z0-9 ]", " ", s or "").lower().split()
            if t and t not in STOP]
    return " ".join(toks)


def _abbrev(short, long_):
    """Is `short` a dropped-letter abbreviation of `long_`? INTL -> INTERNATIONAL."""
    if len(short) < 3 or short[0] != long_[0] or len(short) >= len(long_):
        return False
    i = 0
    for ch in long_:
        if i < len(short) and ch == short[i]:
            i += 1
    return i == len(short)


def tok_match(a, b):
    """Compare issuer names token-wise, allowing 13F's hand-typed abbreviations.

    "RESTAURANT BRANDS INTL INC" is "Restaurant Brands International Inc.", and
    "HERTZ GLOBAL HLDGS" is "Hertz Global Holdings". But a loose match is only
    trusted when at least one other token matched exactly - without that guard
    "IDT" matches "INNODATA", since its letters happen to appear in order.
    """
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return False
    if ta == tb:
        return True

    if len(ta) == len(tb):
        exact = loose = 0
        for x, y in zip(ta, tb):
            if x == y:
                exact += 1
                continue
            short, long_ = (x, y) if len(x) < len(y) else (y, x)
            if long_.startswith(short) and len(short) >= 4:
                loose += 1
                continue
            if _abbrev(short, long_):
                loose += 1
                continue
            return False
        # a fuzzy token only counts when something else pinned the name down
        return loose == 0 or exact >= 1

    short_t, long_t = (ta, tb) if len(ta) < len(tb) else (tb, ta)
    return len(short_t) >= 2 and short_t == long_t[:len(short_t)]


def fund_positions(company_name, cusip=None):
    """Which tracked funds hold this security, per the latest 13F.

    Matched on the 13F issuer name (and CUSIP when we have one), because 13F
    tables carry no ticker.
    """
    import glob
    idx = {}
    for fp in glob.glob(os.path.join(DATA, "holdings", "*.json")):
        d = json.load(open(fp))
        for cu, pos in d["positions"].items():
            idx.setdefault(cu, []).append({
                "fund": d["fund"], "person": d["person"], "cik": d["cik"],
                "period": d["current"], "filed": d["filed"], **pos})
    if not idx:
        return []
    want = norm_name(company_name)
    if not want:
        return []
    hits = []
    for cu, holders in idx.items():
        if cusip and cu.upper() == cusip.upper():
            hits.extend(holders)
            continue
        # the CUSIP matches or it doesn't - either way take EVERY holder of it,
        # not just the first one that matched the name
        if any(tok_match(norm_name(h.get("issuer")), want) for h in holders):
            hits.extend(holders)
    # dedupe by fund, keep the largest
    best = {}
    for h in hits:
        k = h["cik"]
        if k not in best or (h.get("value") or 0) > (best[k].get("value") or 0):
            best[k] = h
    return sorted(best.values(), key=lambda h: -(h.get("value") or 0))


def build(ticker, limit=90, verbose=True):
    hit = resolve(ticker)
    if not hit:
        raise SystemExit(f"unknown ticker: {ticker}")
    cik, name = hit
    if verbose:
        print(f"{ticker} -> {name} (CIK {cik})")

    fils = edgar.recent_filings(cik, forms={"4", "4/A", "144", "SC 13D", "SC 13D/A",
                                            "SC 13G", "SC 13G/A"}, limit=limit)
    trades, presales, holders = [], [], []
    watch_ciks = set()
    for src in ("funds.json", "ciks.json"):
        p = os.path.join(DATA, src)
        if not os.path.exists(p):
            continue
        d = json.load(open(p))
        vals = d.get("firms", d).values() if src == "ciks.json" else d.values()
        watch_ciks |= {v["cik"].lstrip("0") for v in vals}

    for f in fils:
        try:
            if f["form"].startswith("4"):
                rec = edgar.parse_form4(edgar.fetch(f["raw_url"]), f["raw_url"])
                if not rec or rec["issuer_cik"].lstrip("0") != cik.lstrip("0"):
                    continue
                owner = next((o for o in rec["owners"]), {}) or {}
                for t in rec["transactions"]:
                    if t["code"] not in ("P", "S") or not t["shares"]:
                        continue
                    trades.append({
                        "filed": f["filed"], "date": t["date"],
                        "who": owner.get("name", ""), "who_cik": owner.get("cik", ""),
                        "roles": owner.get("roles", []),
                        "action": t["action"], "shares": t["shares"],
                        "price": t["price"], "value": t["value"],
                        "shares_after": t["shares_after"],
                        "rule_10b5_1": rec["rule_10b5_1"],
                        "tracked": owner.get("cik", "").lstrip("0") in watch_ciks,
                        "url": f["index"],
                    })
            elif f["form"] == "144":
                rec = edgar.parse_144(edgar.fetch(f["raw_url"]), f["raw_url"])
                if not rec or not (rec["units"] or rec["value"]):
                    continue
                presales.append({
                    "filed": f["filed"], "who": rec["seller"],
                    "roles": rec["relationships"], "units": rec["units"],
                    "value": rec["value"], "sale_date": rec["sale_date"],
                    "url": f["index"],
                })
            else:
                rec = edgar.parse_13d(edgar.fetch(f.get("raw_url") or f["url"]), f["url"])
                best = max(rec["persons"], key=lambda p: (p.get("percent") or 0),
                           default=None) if rec.get("persons") else None
                holders.append({
                    "filed": f["filed"], "form": f["form"],
                    "who": (best or {}).get("name") or f["entity"],
                    "percent": (best or {}).get("percent"),
                    "shares": (best or {}).get("shares"),
                    "url": f["index"],
                })
        except Exception as e:
            if verbose:
                print(f"  skip {f['form']} {f['accession']}: {e}")

    trades.sort(key=lambda t: (t["filed"], t["date"]), reverse=True)
    presales.sort(key=lambda p: p["filed"], reverse=True)
    holders.sort(key=lambda h: h["filed"], reverse=True)

    buys = [t for t in trades if t["action"] == "buy"]
    sells = [t for t in trades if t["action"] == "sell"]
    # score each filing against what the stock actually did afterwards
    px = px_mod.load(ticker)
    if px:
        for t in trades:
            t["perf"] = px_mod.score(px, t["date"] or t["filed"], ref_price=t.get("price"))
        for pz in presales:
            unit = (pz["value"] / pz["units"]) if pz.get("units") else None
            pz["perf"] = px_mod.score(px, pz["filed"], ref_price=unit)

    cusip = next((h.get("cusip") for h in holders if h.get("cusip")), None)
    funds_hold = fund_positions(name, cusip)

    # "lately" needs to be explicit: say how stale the newest signal is
    import datetime as _dt
    today = _dt.date.today()
    def age(d):
        try:
            return (today - _dt.date.fromisoformat(d)).days
        except Exception:
            return None
    newest_trade = trades[0]["filed"] if trades else None
    newest_any = fils[0]["filed"] if fils else None
    out = {
        "ticker": ticker.upper(), "name": name, "cik": cik,
        "generated": edgar.now_iso(),
        "trades": trades, "presales": presales, "holders": holders,
        "price": ({"now": px.get("price"), "currency": px.get("currency"),
                   "exchange": px.get("exchange"), "fetched": px.get("fetched"),
                   "source": px.get("source"),
                   "series": px["series"][-180:]} if px else None),
        "funds": funds_hold,
        "recency": {
            "last_open_market_trade": newest_trade,
            "days_since_trade": age(newest_trade) if newest_trade else None,
            "last_filing_of_any_kind": newest_any,
            "days_since_filing": age(newest_any) if newest_any else None,
        },
        "summary": {
            "buys": len(buys), "sells": len(sells),
            "buy_value": sum(t["value"] or 0 for t in buys),
            "sell_value": sum(t["value"] or 0 for t in sells),
            "distinct_buyers": len({t["who_cik"] for t in buys if t["who_cik"]}),
            "distinct_sellers": len({t["who_cik"] for t in sells if t["who_cik"]}),
            "presale_value": sum(p["value"] or 0 for p in presales),
            "sell_avg_price": (sum((t["value"] or 0) for t in sells) /
                               sum((t["shares"] or 0) for t in sells)) if sells and
                               sum((t["shares"] or 0) for t in sells) else None,
            "buy_avg_price": (sum((t["value"] or 0) for t in buys) /
                              sum((t["shares"] or 0) for t in buys)) if buys and
                              sum((t["shares"] or 0) for t in buys) else None,
            "first": trades[-1]["filed"] if trades else None,
            "last": trades[0]["filed"] if trades else None,
        },
    }
    os.makedirs(STOCKS, exist_ok=True)
    json.dump(out, open(os.path.join(STOCKS, f"{ticker.upper()}.json"), "w"), indent=1)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--limit", type=int, default=90)
    a = ap.parse_args()
    for tk in a.tickers:
        t0 = time.time()
        o = build(tk, limit=a.limit)
        s = o["summary"]
        print(f"  {s['buys']} buys / {s['sells']} sells  "
              f"({s['distinct_buyers']} buyers, {s['distinct_sellers']} sellers)  "
              f"{len(o['presales'])} pre-sale notices, {len(o['holders'])} 5% filings "
              f"[{time.time()-t0:.0f}s]")
        print(f"  window {s['first']} -> {s['last']}")
        r = o["recency"]
        if r["days_since_trade"] is not None:
            print(f"  last open-market trade: {r['last_open_market_trade']} "
                  f"({r['days_since_trade']}d ago); last filing of any kind: "
                  f"{r['last_filing_of_any_kind']} ({r['days_since_filing']}d ago)")
        if o.get("price"):
            pr = o["price"]
            sp = s.get("sell_avg_price")
            print(f"  price now ${pr['now']:.2f} ({pr['exchange']})")
            if sp:
                ch = (pr["now"] - sp) / sp * 100
                print(f"  insiders sold at an average ${sp:.2f} -> {ch:+.1f}% since")
        if o["funds"]:
            print(f"  tracked funds holding: {len(o['funds'])}")
            for h in o["funds"][:6]:
                print(f"     {h['fund']:18s} {h['action']:12s} "
                      f"{h['shares']:>12,.0f} sh  ${h['value']:>13,.0f}  ({h['period']})")
        else:
            print("  tracked funds holding: none in latest 13F")
