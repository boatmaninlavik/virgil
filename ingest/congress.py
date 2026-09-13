"""Congressional stock trades from House STOCK Act periodic transaction reports.

Members must report trades within 30 days of notice and 45 of the trade. The
Clerk publishes a per-year index plus one PDF per filing; the PDFs are text,
not scans, so pdftotext -layout recovers the table.

Amounts are disclosed only as ranges ($1,001-$15,000 ...), never exact, and the
filing is the member's own account of a trade up to 45 days old.
"""
import json, os, re, subprocess, sys, time, urllib.request, zipfile, io

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PDFS = os.path.join(DATA, "congress", "pdf")
OUT = os.path.join(DATA, "congress")
UA = "sean@erised.me virgil/0.1"

IDX = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{y}FD.zip"
PDF = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{y}/{doc}.pdf"

TYPE = {"P": "buy", "S": "sell", "E": "exchange"}
# a transaction line carries a type letter, two dates and the low end of a range
ROW = re.compile(
    r"^\s*(?P<owner>SP|JT|DC)?\s*(?P<asset>\S.*?)\s{2,}"
    r"(?P<type>[PSE])(?:\s*\(partial\))?\s+"
    r"(?P<txn>\d{2}/\d{2}/\d{4})\s+(?P<notif>\d{2}/\d{2}/\d{4})\s+"
    r"\$(?P<lo>[\d,]+)\s*-?\s*$")
HI = re.compile(r"\$([\d,]+)")
TICKER = re.compile(r"\(([A-Z][A-Z.\-]{0,6})\)")
DESC = re.compile(r"^\s*D\s*[^:]*:\s*(.+)$")


def fetch(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    r = urllib.request.urlopen(req, timeout=45).read()
    return r if binary else r.decode("utf-8", "replace")


def index(year):
    z = zipfile.ZipFile(io.BytesIO(fetch(IDX.format(y=year), binary=True)))
    name = next(n for n in z.namelist() if n.endswith(".txt"))
    rows = []
    for i, line in enumerate(z.read(name).decode("utf-8", "replace").splitlines()):
        if i == 0:
            continue
        f = line.split("\t")
        if len(f) < 9 or f[4] != "P":
            continue
        rows.append({"last": f[1], "first": f[2], "state": f[5],
                     "year": f[6], "filed": f[7], "doc": f[8].strip()})
    return rows


def pdf_text(year, doc):
    os.makedirs(PDFS, exist_ok=True)
    p = os.path.join(PDFS, f"{doc}.pdf")
    if not os.path.exists(p):
        try:
            open(p, "wb").write(fetch(PDF.format(y=year, doc=doc), binary=True))
        except Exception:
            return None
        time.sleep(0.25)
    try:
        return subprocess.run(["pdftotext", "-layout", p, "-"],
                              capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return None


def parse(text):
    """Pull transaction rows out of one PTR."""
    out, lines = [], (text or "").splitlines()
    for i, line in enumerate(lines):
        m = ROW.match(line)
        if not m:
            continue
        tail = "\n".join(lines[i + 1:i + 5])
        hi = HI.search(tail)
        asset = m.group("asset").strip()
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        tk = TICKER.search(asset) or TICKER.search(nxt)
        desc = ""
        for j in range(i + 1, min(i + 6, len(lines))):
            d = DESC.match(lines[j])
            if d:
                desc = d.group(1).strip()
                break
        if not TICKER.search(asset) and nxt.strip() and not ROW.match(nxt):
            asset = (asset + " " + nxt.strip().split("  ")[0]).strip()
        out.append({
            "owner": m.group("owner") or "SELF",
            "asset": re.sub(r"\s+", " ", asset)[:120],
            "ticker": tk.group(1) if tk else None,
            "action": TYPE.get(m.group("type"), m.group("type")),
            "partial": "(partial)" in line,
            "txn_date": m.group("txn"),
            "notified": m.group("notif"),
            "amount_low": int(m.group("lo").replace(",", "")),
            "amount_high": int(hi.group(1).replace(",", "")) if hi else None,
            "description": desc[:160],
        })
    return out


def build(years=(2026,), limit=None, verbose=True):
    os.makedirs(OUT, exist_ok=True)
    cache_p = os.path.join(OUT, "filings.json")
    cache = json.load(open(cache_p)) if os.path.exists(cache_p) else {}
    events, seen_new = [], 0

    for y in years:
        rows = index(y)
        if verbose:
            print(f"{y}: {len(rows)} PTR filings")
        for n, r in enumerate(rows[:limit] if limit else rows, 1):
            key = r["doc"]
            if key in cache:
                txns = cache[key]["txns"]
            else:
                txns = parse(pdf_text(y, key)) or []
                cache[key] = {"member": f"{r['first']} {r['last']}".strip(),
                              "state": r["state"], "filed": r["filed"],
                              "doc": key, "year": y, "txns": txns}
                seen_new += 1
                if seen_new % 25 == 0:
                    json.dump(cache, open(cache_p, "w"))
                    if verbose:
                        print(f"  parsed {n}/{len(rows)}", flush=True)
            for t in txns:
                events.append({**t, "member": cache[key]["member"],
                               "state": cache[key]["state"],
                               "filed": cache[key]["filed"], "doc": key, "year": y})
    json.dump(cache, open(cache_p, "w"))

    def iso(d):
        try:
            m, dd, yy = d.split("/")
            return f"{yy}-{m.zfill(2)}-{dd.zfill(2)}"
        except Exception:
            return d
    for e in events:
        e["txn_iso"] = iso(e["txn_date"])
        e["filed_iso"] = iso(e["filed"])
    events.sort(key=lambda e: (e["txn_iso"], e["filed_iso"]), reverse=True)
    json.dump(events, open(os.path.join(OUT, "trades.json"), "w"), indent=1)

    by_ticker = {}
    for e in events:
        if e["ticker"]:
            by_ticker.setdefault(e["ticker"], []).append(e)
    json.dump(by_ticker, open(os.path.join(OUT, "by_ticker.json"), "w"))
    return events, by_ticker


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2026")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    t0 = time.time()
    ev, bt = build(years=[int(y) for y in a.years.split(",")], limit=a.limit)
    members = len({e["member"] for e in ev})
    print(f"\n{len(ev)} transactions from {members} members, "
          f"{len(bt)} tickers, in {time.time()-t0:.0f}s")
    for e in ev[:10]:
        amt = f"${e['amount_low']:,}-{e['amount_high']:,}" if e["amount_high"] else f"${e['amount_low']:,}+"
        print(f"  {e['txn_iso']}  {e['member'][:22]:24s} {e['action']:8s} "
              f"{(e['ticker'] or '—'):6s} {amt:>24s}")
