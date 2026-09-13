"""Re-point stale funds at whichever entity in their family files 13F today.

Groups restructure: the CIK that filed in 2013 is often dead while a sibling
files every quarter. Picking by name alone silently serves a decade-old book.
"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

FAMILIES = {
    "Egerton": r"^EGERTON",
    "KKR (Credit)": r"^KKR",
    "HealthCor": r"^HEALTHCOR",
    "JANA Partners": r"^JANA PARTNERS",
    "Pentwater": r"^PENTWATER",
    "Greenlight Capital": r"^GREENLIGHT CAPITAL",
    "BlackRock": r"^BLACKROCK",
    "Susquehanna": r"^SUSQUEHANNA",
    "Scion": r"^SCION ASSET",
    "Vanguard": r"^VANGUARD GROUP",
}


def latest_13f(cik):
    try:
        d = edgar.submissions(cik)
    except Exception:
        return None, None
    r = d["filings"]["recent"]
    best = None
    for i in range(len(r["form"])):
        if r["form"][i] == "13F-HR":
            p = r.get("reportDate", [""] * len(r["form"]))[i]
            if p and (best is None or p > best):
                best = p
    return best, d.get("name")


if __name__ == "__main__":
    rows = []
    for line in open(os.path.join(DATA, "cik-lookup.txt"), encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if not line.endswith(":"):
            continue
        n, c = line[:-1].rsplit(":", 1)
        rows.append((n, c.zfill(10)))

    funds = json.load(open(os.path.join(DATA, "funds.json")))
    changed = 0
    for disp, pat in FAMILIES.items():
        if disp not in funds:
            continue
        cur_cik = funds[disp]["cik"]
        cur_p, _ = latest_13f(cur_cik)
        rx = re.compile(pat, re.I)
        cands = sorted({(n, c) for n, c in rows if rx.match(n)}, key=lambda x: len(x[0]))[:30]
        best = (cur_p, cur_cik, funds[disp]["edgar_name"])
        for n, c in cands:
            if c == cur_cik:
                continue
            p, nm = latest_13f(c)
            if p and (best[0] is None or p > best[0]):
                best = (p, c, nm)
        if best[1] != cur_cik:
            print(f"{disp:20s} {cur_cik} ({cur_p}) -> {best[1]} ({best[0]})  {best[2][:44]}")
            funds[disp].update({"cik": best[1], "edgar_name": best[2]})
            changed += 1
        else:
            print(f"{disp:20s} keeps {cur_cik}; newest 13F-HR is {cur_p} "
                  f"(no sibling files more recently)")
    json.dump(funds, open(os.path.join(DATA, "funds.json"), "w"), indent=2)
    print(f"\nre-pointed {changed} funds")
