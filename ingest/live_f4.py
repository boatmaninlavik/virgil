"""Live market-wide Form 4 lane: new insider trades within minutes, any ticker.

The watchlist poller (poll.py) runs every 60s but only sees the 48 entities we
follow. The per-stock view is fed by insiders_by_ticker.py, which reads EDGAR's
*daily index* — and that file is only published after the close, so an insider
sale filed at 16:10 could not appear until the next morning's scan.

EDGAR's `getcurrent` feed has no such lag: it lists filings within a minute or
two of acceptance. So this polls that feed, parses whatever is new, and merges
it straight into the per-ticker index. The daily scan still runs and remains
the source of truth; this only closes the intraday gap ahead of it.

    python3 ingest/live_f4.py
"""
import json, os, re, sys, time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LIVE = os.path.join(DATA, "live_f4.json")           # merged by the daily scan
INDEX = os.path.join(DATA, "insiders_by_ticker.json")
KEEP = {"P", "S"}                                   # open-market intent only
BLANK = {"", "N/A", "NONE", "NA", "N.A."}           # issuer has no listed symbol
FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
        "&type=4&company=&dateb=&owner=include&count=100&start={}&output=atom")
HOLD_DAYS = 6          # keep live rows until the daily scan has certainly run

ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)
HREF = re.compile(r'href="([^"]*-index\.htm)"')
TERM = re.compile(r'term="([^"]*)"')
FILED = re.compile(r"Filed:</b>\s*(\d{4})-(\d{2})-(\d{2})")


def recent():
    """Accession paths of Form 4s currently on the live feed, newest first.

    `type=4` is a prefix match on EDGAR's side, so the feed also returns 424B2
    and friends; the category term is the exact form type, so filter on that.
    Each filing appears once per party (Reporting and Issuer) under a different
    CIK path, so dedupe on the accession number or every trade lands twice.
    """
    seen, out = set(), []
    for start in (0, 100, 200):
        try:
            xml = edgar.fetch(FEED.format(start))
        except Exception:
            break
        for body in ENTRY.findall(xml):
            term = TERM.search(body)
            if not term or term.group(1) != "4":
                continue
            href = HREF.search(body)
            if not href:
                continue
            # .../Archives/edgar/data/{cik}/{accnodash}/{accession}-index.htm
            parts = href.group(1).split("/")
            try:
                cik = parts[parts.index("data") + 1]
            except (ValueError, IndexError):
                continue
            accession = parts[-1].replace("-index.htm", "")
            if accession in seen:
                continue
            seen.add(accession)
            path = f"edgar/data/{cik}/{accession}.txt"
            f = FILED.search(body)
            out.append({"path": path, "filed": "".join(f.groups()) if f else
                        date.today().strftime("%Y%m%d"),
                        "url": "https://www.sec.gov/Archives/" + path})
    return out


def accession_of(url):
    """The accession number identifies a filing; the CIK in its path does not."""
    return (url or "").rsplit("/", 1)[-1].replace("-index.htm", "").replace(".txt", "")


def prune(live):
    """Drop rows the daily scan has long since absorbed."""
    floor = (date.today() - timedelta(days=HOLD_DAYS)).strftime("%Y%m%d")
    return {k: v for k, v in live.items() if not v or v.get("filed", "") >= floor}


def parse(rows):
    """Fetch and parse filings, returning cache entries keyed by accession path.

    Same entry shape as insiders_by_ticker.scan_day, so the two lanes merge
    without translation and build_index consumes either one.
    """
    bodies = edgar.fetch_many([r["url"] for r in rows], on_error=lambda u, e: None)
    out = {}
    for r in rows:
        xml = edgar.extract_ownership(bodies.get(r["url"]))
        rec = edgar.parse_form4(xml, r["url"]) if xml else None
        if not rec:
            out[r["path"]] = None
            continue
        txns = [t for t in rec["transactions"]
                if t["code"] in KEEP and t["shares"] and t["table"] == "I"]
        if not txns or rec.get("ticker", "").upper() in BLANK:
            out[r["path"]] = None
            continue
        owner = next((o for o in rec["owners"]), {}) or {}
        out[r["path"]] = {
            "filed": r["filed"], "ticker": rec["ticker"].upper(),
            "issuer": rec["issuer_name"], "issuer_cik": rec["issuer_cik"],
            "who": owner.get("name", ""), "who_cik": owner.get("cik", ""),
            "roles": owner.get("roles", []),
            "rule_10b5_1": rec["rule_10b5_1"],
            "t": [[t["date"], t["code"], t["shares"], t["price"], t["value"]]
                  for t in txns],
            "url": r["url"].replace(".txt", "-index.htm"),
        }
    return out


def merge_into_index(live):
    """Fold live rows into the per-ticker index the page reads.

    The index is rebuilt wholesale by the daily scan; here we only add, keyed
    on filing URL so a row the daily scan already wrote is never duplicated.
    """
    if not os.path.exists(INDEX):
        return 0, 0
    idx = json.load(open(INDEX))
    added, touched = 0, set()
    for v in live.values():
        if not v:
            continue
        rows = idx.setdefault(v["ticker"], [])
        have = {(accession_of(r.get("url")), r.get("date"), r.get("shares"))
                for r in rows}
        acc = accession_of(v["url"])
        for d, code, sh, px, val in v["t"]:
            row = {"who": v["who"], "roles": v["roles"], "issuer": v["issuer"],
                   "action": "buy" if code == "P" else "sell",
                   "date": d, "filed": v["filed"], "shares": sh,
                   "price": px, "value": val,
                   "rule_10b5_1": v["rule_10b5_1"], "url": v["url"]}
            if (acc, d, sh) in have:
                continue
            have.add((acc, d, sh))
            rows.append(row)
            added += 1
            touched.add(v["ticker"])
    for t in touched:
        idx[t].sort(key=lambda r: (r["date"] or "", r["filed"]), reverse=True)
        idx[t] = idx[t][:40]
    if added:
        json.dump(idx, open(INDEX, "w"), separators=(",", ":"))
    return added, len(touched)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=140,
                    help="cap per cycle; Section 16 filings burst after the "
                         "close and the daily scan backstops any overflow")
    a = ap.parse_args()

    live = prune(json.load(open(LIVE))) if os.path.exists(LIVE) else {}
    todo = [r for r in recent() if r["path"] not in live][:a.max]
    if not todo:
        print("live: nothing new")
        sys.exit(0)

    t0 = time.time()
    live.update(parse(todo))
    json.dump(live, open(LIVE, "w"), separators=(",", ":"))
    added, tickers = merge_into_index(live)
    kept = sum(1 for v in live.values() if v)
    print(f"live: {len(todo)} new filings, {added} trades across {tickers} "
          f"tickers ({kept} held), {time.time()-t0:.0f}s")
