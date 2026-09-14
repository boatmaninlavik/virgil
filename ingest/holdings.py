"""13F holdings for the tracked funds, inverted so you can ask about a stock.

13F is not indexed by security anywhere — it is filed per manager. To answer
"does Ackman own this?" you have to read every manager's table and invert it.
Doing that for 28 tracked funds is cheap; doing it for all ~5,000 filers is the
same code with a bigger list.

Quarterly and lagged 45-135 days by law. This is the slow lane by definition.
"""
import json, os, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
HOLD = os.path.join(DATA, "holdings")
CUSIP_MAP = os.path.join(DATA, "cusip_map.json")


def infotable_url(cik, accession):
    acc = accession.replace("-", "")
    idx = json.loads(edgar.fetch(
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/index.json"))
    best = None
    for it in idx["directory"]["item"]:
        n = it["name"].lower()
        if not n.endswith(".xml") or n == "primary_doc.xml":
            continue
        # prefer an explicitly named information table
        if "infotable" in n or "information" in n or "form13f" in n:
            best = it["name"]
            break
        best = best or it["name"]
    if not best:
        return None
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{best}"


def fetch_fund(cik, label, quarters=2, verbose=True):
    fils = edgar.recent_filings(cik, forms={"13F-HR"}, limit=quarters)
    out = {}
    for f in fils:
        try:
            u = infotable_url(cik, f["accession"])
            if not u:
                continue
            rows = edgar.parse_13f(edgar.fetch(u))
        except Exception as e:
            if verbose:
                print(f"    {label}: {f['period']} failed ({e})")
            continue
        if rows:
            out[f["period"]] = {"filed": f["filed"], "rows": rows,
                                "accession": f["accession"]}
    return out


def diff(cur, prev):
    """Position changes between two quarters, keyed by CUSIP."""
    def agg(rows):
        m = defaultdict(lambda: {"shares": 0.0, "value": 0.0, "issuer": "", "class": ""})
        for r in rows:
            if r.get("put_call"):          # options are a different exposure
                continue
            a = m[r["cusip"]]
            a["shares"] += r["shares"]
            a["value"] += r["value"]
            a["issuer"] = a["issuer"] or r["issuer"]
            a["class"] = a["class"] or r["class"]
        return m

    c, p = agg(cur), agg(prev or [])
    out = {}
    for cusip in set(c) | set(p):
        cs, ps = c.get(cusip), p.get(cusip)
        cur_sh = cs["shares"] if cs else 0.0
        prev_sh = ps["shares"] if ps else 0.0
        if cur_sh == prev_sh == 0:
            continue
        if prev_sh == 0:
            action = "new position"
        elif cur_sh == 0:
            action = "exited"
        elif cur_sh > prev_sh * 1.005:
            action = "added"
        elif cur_sh < prev_sh * 0.995:
            action = "trimmed"
        else:
            action = "held"
        out[cusip] = {
            "issuer": (cs or ps)["issuer"], "class": (cs or ps)["class"],
            "shares": cur_sh, "prev_shares": prev_sh,
            "value": cs["value"] if cs else 0.0,
            "delta_shares": cur_sh - prev_sh,
            "delta_pct": ((cur_sh - prev_sh) / prev_sh * 100) if prev_sh else None,
            "action": action,
        }
    return out


def build(verbose=True, only=None):
    """only: a CIK. Adding one investor should not re-fetch 145 other funds."""
    funds = json.load(open(os.path.join(DATA, "funds.json")))
    if only:
        want = str(only).zfill(10)
        funds = {k: v for k, v in funds.items()
                 if str(v.get("cik", "")).zfill(10) == want}
    os.makedirs(HOLD, exist_ok=True)
    index = defaultdict(list)
    meta = {}

    for key, v in funds.items():
        cik, label = v["cik"], v["display"]
        data = fetch_fund(cik, label, verbose=verbose)
        if not data:
            if verbose:
                print(f"  {label:20s} no 13F-HR holdings (13F-NT or none)")
            meta[label] = {"periods": [], "person": v["person"], "cik": cik}
            continue
        periods = sorted(data)
        cur_p = periods[-1]
        prev_p = periods[-2] if len(periods) > 1 else None
        d = diff(data[cur_p]["rows"], data[prev_p]["rows"] if prev_p else None)

        json.dump({"fund": label, "person": v["person"], "cik": cik,
                   "current": cur_p, "previous": prev_p,
                   "filed": data[cur_p]["filed"], "positions": d},
                  open(os.path.join(HOLD, f"{cik}.json"), "w"), indent=1)
        meta[label] = {"periods": periods, "person": v["person"], "cik": cik,
                       "filed": data[cur_p]["filed"], "positions": len(d)}

        for cusip, pos in d.items():
            if pos["action"] == "held" and pos["shares"] == 0:
                continue
            index[cusip].append({
                "fund": label, "person": v["person"], "cik": cik,
                "period": cur_p, "filed": data[cur_p]["filed"],
                **{k: pos[k] for k in ("issuer", "shares", "prev_shares", "value",
                                       "delta_shares", "delta_pct", "action")},
            })
        if verbose:
            print(f"  {label:20s} {cur_p}  {len(d):5d} positions  "
                  f"(vs {prev_p or 'n/a'})")

    for cusip in index:
        index[cusip].sort(key=lambda h: -(h["value"] or 0))
    # the sharded ticker index in ui/data supersedes this 53 MB duplicate
    json.dump(meta, open(os.path.join(DATA, "holdings_meta.json"), "w"), indent=1)
    return index, meta


if __name__ == "__main__":
    import argparse
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--only", help="a single CIK, for a freshly added fund")
    _a = _ap.parse_args()
    t0 = time.time()
    idx, meta = build(only=_a.only)
    covered = sum(1 for m in meta.values() if m.get("periods"))
    print(f"\n{covered}/{len(meta)} funds with holdings; "
          f"{len(idx)} distinct securities indexed in {time.time()-t0:.0f}s")
