"""Resolve EDGAR CIKs for our watchlist of funds and public finance firms."""
import json, re, sys, time, urllib.request, urllib.parse

UA = "sean@erised.me virgil/0.1"

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")

def search_company(name):
    """EDGAR company-name search -> [(cik, name)]"""
    q = urllib.parse.quote(name)
    url = (f"https://www.sec.gov/cgi-bin/browse-edgar?company={q}"
           "&CIK=&type=&dateb=&owner=include&count=10&action=getcompany&output=atom")
    try:
        xml = get(url)
    except Exception as e:
        return []
    out = []
    # single-result pages embed CIK directly
    m = re.search(r"<CIK>(\d+)</CIK>", xml)
    if m:
        nm = re.search(r"<conformed-name>(.*?)</conformed-name>", xml)
        return [(m.group(1).zfill(10), nm.group(1) if nm else name)]
    for cik, nm in re.findall(r"CIK=(\d{10}).*?<title>(.*?)</title>", xml, re.S):
        out.append((cik, nm))
    return out

FUNDS = [
    "Pershing Square Capital Management", "Point72 Asset Management",
    "Berkshire Hathaway Inc", "Bridgewater Associates", "Citadel Advisors",
    "Millennium Management", "Elliott Investment Management", "Third Point",
    "Greenlight Capital", "Appaloosa", "Scion Asset Management",
    "Tiger Global Management", "Coatue Management", "Duquesne Family Office",
    "Soros Fund Management", "ValueAct", "Trian Fund Management",
    "Icahn Carl", "Baupost Group", "Lone Pine Capital", "Viking Global Investors",
    "D1 Capital Partners", "Renaissance Technologies", "Two Sigma Investments",
    "AQR Capital Management", "Altimeter Capital Management",
]
FIRM_TICKERS = ["BX","KKR","APO","CG","ARES","OWL","TPG","BN","GS","MS","EVR",
                "LAZ","PJT","MC","HLI","PWP","JEF","BLK","SCHW","RJF"]

if __name__ == "__main__":
    result = {"funds": {}, "firms": {}}

    # public companies via the official ticker->CIK map
    tick = json.loads(get("https://www.sec.gov/files/company_tickers.json"))
    bysym = {v["ticker"]: (str(v["cik_str"]).zfill(10), v["title"]) for v in tick.values()}
    for t in FIRM_TICKERS:
        if t in bysym:
            cik, nm = bysym[t]
            result["firms"][t] = {"cik": cik, "name": nm}
            print(f"FIRM  {t:5s} {cik}  {nm}")
        else:
            print(f"FIRM  {t:5s} !! not found")

    for f in FUNDS:
        hits = search_company(f)
        if hits:
            cik, nm = hits[0]
            result["funds"][f] = {"cik": cik, "name": nm.strip()}
            print(f"FUND  {cik}  {nm.strip()[:60]}   <- {f}")
        else:
            print(f"FUND  !! no match for {f}")
        time.sleep(0.15)

    with open(sys.argv[1] if len(sys.argv) > 1 else "ciks.json", "w") as fh:
        json.dump(result, fh, indent=2)
    print(f"\nwrote {len(result['funds'])} funds, {len(result['firms'])} firms")
