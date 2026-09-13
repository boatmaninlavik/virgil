"""Index of every entity that files like an investor, for name lookup in the UI.

The page needs to answer "is this person or firm on EDGAR at all?" without a
backend. data.sec.gov sends CORS headers, but the company *search* lives on
www.sec.gov, which does not - so the browser cannot resolve a name itself and
the index has to ship with the page.

Scope is the investor universe rather than all of EDGAR: whoever filed a 13F,
a 13D or a 13G. That is funds, family offices, and the individuals who file
activist stakes in their own name. Corporate insiders who only ever file a
Form 4 are excluded, since including them means shipping a few hundred thousand
names to answer a question nobody asks.

    python3 ingest/filers.py --quarters 8
"""
import json, os, re, sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "filers.json")
KEEP = re.compile(r"^(13F-HR|SC 13D|SC 13G|SCHEDULE 13[DG])", re.I)
# Fixed-width columns shift when a form name runs long ("SCHEDULE 13D/A"), which
# pushed "/A" into the CIK field. Anchor on the date instead, which is rigid.
ROW = re.compile(r"^(\S[^ ].{0,14}?)\s{2,}(.+?)\s{2,}(\d{1,10})\s+"
                 r"(\d{4}-\d{2}-\d{2})\s+(\S+)\s*$")


def quarters(n):
    y, q = date.today().year, (date.today().month - 1) // 3 + 1
    out = []
    for _ in range(n):
        out.append((y, q))
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return out


def scan(y, q, acc):
    url = f"https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/form.idx"
    try:
        txt = edgar.fetch(url)
    except Exception as e:
        print(f"  {y}Q{q}: unavailable ({type(e).__name__})", flush=True)
        return 0
    n = 0
    for line in txt.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        form, name, cik, filed = (m.group(1).strip(), m.group(2).strip(),
                                  m.group(3).strip(), m.group(4).strip())
        if not KEEP.match(form) or not cik.isdigit():
            continue
        rec = acc[cik.zfill(10)]
        rec["name"] = rec.get("name") or name
        rec.setdefault("forms", set()).add(form.split("/")[0].upper())
        if filed > rec.get("last", ""):
            rec["last"] = filed
        n += 1
    print(f"  {y}Q{q}: {n} filings", flush=True)
    return n


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarters", type=int, default=8)
    a = ap.parse_args()

    acc = defaultdict(dict)
    for y, q in quarters(a.quarters):
        scan(y, q, acc)

    out = {}
    for cik, r in acc.items():
        if not r.get("name"):
            continue
        out[cik] = {"n": r["name"], "f": sorted(r["forms"]), "d": r.get("last", "")}
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    mb = os.path.getsize(OUT) / 1048576
    print(f"\n{len(out)} investor-filers indexed  ({mb:.1f} MB)")
