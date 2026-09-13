"""Poll EDGAR for new filings from the watchlist and emit a normalized feed.

Roles, kept separate on purpose:
  fund_trade  - a watched fund/principal was the reporting owner (Ackman & co.)
  insider     - an insider of a watched public firm traded that firm's stock
  activist    - SC 13D / 13D/A: a >5% activist position changed
  portfolio   - 13F-HR quarterly holdings snapshot (lagged 45-135 days)
"""
import json, os, re, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
STATE = os.path.join(DATA, "state.json")

FAST_FORMS = {"4", "4/A", "SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A",
              "SC 13G", "SC 13G/A", "3", "144"}
SLOW_FORMS = {"13F-HR", "13F-HR/A"}


def load_watchlist():
    funds = json.load(open(os.path.join(DATA, "funds.json")))
    firms = json.load(open(os.path.join(DATA, "ciks.json")))["firms"]
    wl = []
    for k, v in funds.items():
        wl.append({"kind": "fund", "cik": v["cik"], "label": v["display"],
                   "person": v["person"], "title": v["title"],
                   "edgar_name": v["edgar_name"]})
    for tkr, v in firms.items():
        wl.append({"kind": "firm", "cik": v["cik"], "label": v["name"],
                   "ticker": tkr, "person": None, "title": None,
                   "edgar_name": v["name"]})
    return wl


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"seen": {}, "last_run": None}


def save_state(s):
    json.dump(s, open(STATE, "w"))


def classify(rec, watch):
    """Which role does this Form 4 play for the entity we were watching?"""
    wcik = watch["cik"].lstrip("0")
    if rec["issuer_cik"].lstrip("0") == wcik:
        return "insider"
    for o in rec["owners"]:
        if o["cik"].lstrip("0") == wcik:
            return "fund_trade"
    return "related"


def form4_events(f, watch):
    xml = edgar.fetch(f.get("raw_url") or f["url"])
    rec = edgar.parse_form4(xml, f["url"])
    if not rec:
        return []
    role = classify(rec, watch)
    evs = []
    for t in rec["transactions"]:
        if not t["shares"]:
            continue
        person = next((o for o in rec["owners"]
                       if o["cik"].lstrip("0") != rec["issuer_cik"].lstrip("0")), None)
        evs.append({
            "type": role,
            "form": f["form"],
            "filed": f["filed"],
            "txn_date": t["date"],
            "watch_cik": watch["cik"],
            "watch_label": watch["label"],
            "watch_kind": watch["kind"],
            "principal": watch.get("person"),
            "principal_title": watch.get("title"),
            "actor": (person or {}).get("name") or watch["label"],
            "actor_roles": (person or {}).get("roles") or [],
            "issuer": rec["issuer_name"],
            "ticker": rec["ticker"],
            "security": t["security"],
            "action": t["action"],
            "code": t["code"],
            "shares": t["shares"],
            "price": t["price"],
            "value": t["value"],
            "shares_after": t["shares_after"],
            "rule_10b5_1": rec["rule_10b5_1"],
            "is_signal": t["is_signal"],
            "category": "signal" if t["is_signal"] else "comp",
            "url": f["index"],
            "accession": f["accession"],
        })
    return evs


def form144_event(f, watch):
    """Intent to sell, filed before the trade. Leads the Form 4 confirmation."""
    rec = edgar.parse_144(edgar.fetch(f.get("raw_url") or f["url"]), f["url"])
    if not rec or not (rec["units"] or rec["value"]):
        return []
    return [{
        "type": "presale",
        "form": "144",
        "filed": f["filed"],
        "txn_date": rec["sale_date"] or f["filed"],
        "watch_cik": watch["cik"],
        "watch_label": watch["label"],
        "watch_kind": watch["kind"],
        "principal": watch.get("person"),
        "principal_title": watch.get("title"),
        "actor": rec["seller"] or watch["label"],
        "actor_roles": rec["relationships"],
        "issuer": rec["issuer_name"],
        "issuer_cik": rec["issuer_cik"],
        "ticker": "",
        "security": rec["tranches"][0]["security"] if rec["tranches"] else "",
        "action": "intends to sell",
        "code": "144",
        "shares": rec["units"] or None,
        "price": None,
        "value": rec["value"] or None,
        "shares_after": None,
        "rule_10b5_1": False,
        "is_signal": True,
        "category": "signal",
        "url": f["index"],
        "accession": f["accession"],
    }]


POSITIONS = os.path.join(DATA, "positions.json")


def load_positions():
    return json.load(open(POSITIONS)) if os.path.exists(POSITIONS) else {}


def save_positions(p):
    json.dump(p, open(POSITIONS, "w"), indent=1)


def schedule_event(f, watch, positions=None):
    """Schedule 13D/G. The cover page carries the stake; the delta against the
    previous amendment is the part that actually says something."""
    subj = edgar.subject_company(f["headers_url"]) or {}
    rec = None
    try:
        rec = edgar.parse_13d(edgar.fetch(f.get("raw_url") or f["url"]), f["url"])
    except Exception:
        pass
    rec = rec or {"persons": [], "purpose": "", "cusip": "", "amendment_no": ""}

    # the reporting group all report the same aggregate; take the largest
    best = max(rec["persons"], key=lambda p: (p.get("percent") or 0),
               default=None) if rec["persons"] else None
    pct = (best or {}).get("percent")
    shares = (best or {}).get("shares")

    issuer = subj.get("name") or rec.get("issuer_name") or f["entity"]
    icik = subj.get("cik") or rec.get("issuer_cik") or ""

    prev_pct = prev_shares = None
    if positions is not None and icik:
        key = f"{watch['cik']}:{icik}"
        hist = positions.setdefault(key, [])
        prior = [h for h in hist if h["accession"] != f["accession"]]
        if prior:
            last = sorted(prior, key=lambda h: h["filed"])[-1]
            prev_pct, prev_shares = last.get("percent"), last.get("shares")
        if not any(h["accession"] == f["accession"] for h in hist):
            hist.append({"accession": f["accession"], "filed": f["filed"],
                         "percent": pct, "shares": shares,
                         "amendment": rec.get("amendment_no", "")})
            hist.sort(key=lambda h: h["filed"])

    if pct is not None and prev_pct is not None:
        d = pct - prev_pct
        action = ("raised stake" if d > 0.05 else
                  "trimmed stake" if d < -0.05 else "restated stake")
    elif "/A" in f["form"]:
        action = "13D amendment"
    else:
        action = "new >5% stake"

    return [{
        "type": "activist",
        "form": f["form"],
        "filed": f["filed"],
        "txn_date": f["filed"],
        "watch_cik": watch["cik"],
        "watch_label": watch["label"],
        "watch_kind": watch["kind"],
        "principal": watch.get("person"),
        "principal_title": watch.get("title"),
        "actor": watch.get("person") or watch["label"],
        "actor_roles": [],
        "issuer": issuer,
        "issuer_cik": icik,
        "ticker": "",
        "security": rec.get("cusip", ""),
        "action": action,
        "code": "13D",
        "shares": shares, "price": None, "value": None, "shares_after": shares,
        "percent": pct,
        "prev_percent": prev_pct,
        "delta_percent": (pct - prev_pct) if (pct is not None and prev_pct is not None) else None,
        "amendment": rec.get("amendment_no", ""),
        "purpose": rec.get("purpose", "")[:500],
        "group": [p["name"] for p in rec["persons"]][:8],
        "rule_10b5_1": False,
        "is_signal": True,
        "category": "signal",
        "url": f["index"],
        "accession": f["accession"],
    }]


def run(max_per_entity=25, verbose=True, seed_only=False):
    wl = load_watchlist()
    st = load_state()
    seen = st["seen"]
    positions = load_positions()
    events, errors = [], []

    for w in wl:
        try:
            fils = edgar.recent_filings(w["cik"], forms=FAST_FORMS | SLOW_FORMS,
                                        limit=max_per_entity)
        except Exception as e:
            errors.append(f"{w['label']}: {e}")
            continue
        for f in fils:
            key = f["accession"] + ":" + w["cik"]
            if key in seen:
                continue
            if seed_only:                     # mark history as seen, don't parse it
                seen[key] = f["filed"]
                continue
            try:
                if f["form"].startswith("4"):
                    evs = form4_events(f, w)
                elif f["form"] == "144":
                    evs = form144_event(f, w)
                elif "13D" in f["form"]:
                    evs = schedule_event(f, w, positions)
                else:
                    evs = []          # 13F handled by the quarterly job
                events.extend(evs)
                seen[key] = f["filed"]
            except Exception as e:
                errors.append(f"{w['label']} {f['accession']}: {e}")
        if verbose:
            print(f"  {w['label'][:28]:30s} {len(fils):3d} filings", flush=True)

    save_positions(positions)
    st["last_run"] = edgar.now_iso()
    st["seen"] = seen
    save_state(st)
    return events, errors


def reconcile_activist(events):
    """Recompute stake deltas across the whole history.

    EDGAR returns filings newest-first, so a single pass cannot see a filing's
    predecessor. Doing it here makes the result independent of fetch order.
    """
    groups = defaultdict(list)
    for e in events:
        if e.get("type") == "activist" and e.get("issuer_cik"):
            groups[(e["watch_cik"], e["issuer_cik"])].append(e)

    for evs in groups.values():
        evs.sort(key=lambda e: (e["filed"], e.get("amendment") or ""))
        prev = None
        for e in evs:
            pct = e.get("percent")
            e["prev_percent"] = prev
            if pct is not None and prev is not None:
                d = pct - prev
                e["delta_percent"] = d
                e["action"] = ("raised stake" if d > 0.05 else
                               "trimmed stake" if d < -0.05 else "restated stake")
            else:
                e["delta_percent"] = None
                if "/A" in e["form"]:
                    e["action"] = "13D amendment"
                else:
                    e["action"] = "new >5% stake"
            if pct is not None:
                prev = pct
    return events


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10, help="filings scanned per entity")
    ap.add_argument("--seed", action="store_true", help="mark history seen without parsing")
    a = ap.parse_args()

    t0 = time.time()
    evs, errs = run(max_per_entity=a.limit, seed_only=a.seed)
    print(f"\n{len(evs)} new events in {time.time()-t0:.1f}s; {len(errs)} errors")

    path = os.path.join(DATA, "events.json")
    old = json.load(open(path)) if os.path.exists(path) else []
    byk = {f"{e['accession']}:{e.get('ticker')}:{e.get('txn_date')}:{e.get('shares')}:{e.get('actor')}": e
           for e in old + evs}
    allev = reconcile_activist(list(byk.values()))
    allev = sorted(allev, key=lambda e: (e["filed"], e.get("txn_date") or ""), reverse=True)
    json.dump(allev, open(path, "w"), indent=1)
    print(f"feed now holds {len(allev)} events -> data/events.json")
    for e in allev[:12]:
        v = f"${e['value']:,.0f}" if e.get("value") else ""
        print(f"  {e['filed']}  {e['type']:11s} {(e['ticker'] or '—'):6s} "
              f"{e['action']:16s} {v:>14s}  {e['actor'][:34]}")
    if errs:
        print("\nerrors:", *errs[:6], sep="\n  ")
