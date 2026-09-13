"""Match fund names against EDGAR's CIK lookup table, then verify each
candidate actually files 13F/13D so we keep the real management entity."""
import json, re, sys, time, urllib.request

UA = "sean@erised.me virgil/0.1"
LOOKUP = "/Users/thuscodedzara/virgil/data/cik-lookup.txt"

def submissions(cik):
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception:
        return None

# principal -> (search pattern, display name, title)
WANT = [
    (r"^PERSHING SQUARE CAPITAL MANAGEMENT",      "Pershing Square",       "Bill Ackman",        "Founder & CEO"),
    (r"^POINT72 ASSET MANAGEMENT",                "Point72",               "Steve Cohen",        "Chairman & CEO"),
    (r"^BERKSHIRE HATHAWAY INC$",                 "Berkshire Hathaway",    "Warren Buffett",     "Chairman & CEO"),
    (r"^BRIDGEWATER ASSOCIATES, LP",              "Bridgewater",           "Nir Bar Dea",        "CEO"),
    (r"^CITADEL ADVISORS LLC",                    "Citadel",               "Ken Griffin",        "Founder & CEO"),
    (r"^MILLENNIUM MANAGEMENT LLC",               "Millennium",            "Israel Englander",   "Founder & CEO"),
    (r"^ELLIOTT INVESTMENT MANAGEMENT",           "Elliott",               "Paul Singer",        "Founder & Co-CEO"),
    (r"^THIRD POINT LLC",                         "Third Point",           "Dan Loeb",           "Founder & CEO"),
    (r"^GREENLIGHT CAPITAL",                      "Greenlight Capital",    "David Einhorn",      "President"),
    (r"^APPALOOSA LP",                            "Appaloosa",             "David Tepper",       "Founder & President"),
    (r"^SCION ASSET MANAGEMENT",                  "Scion",                 "Michael Burry",      "Founder"),
    (r"^TIGER GLOBAL MANAGEMENT",                 "Tiger Global",          "Chase Coleman",      "Founder"),
    (r"^COATUE MANAGEMENT",                       "Coatue",                "Philippe Laffont",   "Founder"),
    (r"^DUQUESNE FAMILY OFFICE",                  "Duquesne",              "Stanley Druckenmiller","Chairman"),
    (r"^SOROS FUND MANAGEMENT",                   "Soros Fund Mgmt",       "George Soros",       "Founder"),
    (r"^VALUEACT",                                "ValueAct",              "Mason Morfit",       "CEO & CIO"),
    (r"^TRIAN FUND MANAGEMENT",                   "Trian",                 "Nelson Peltz",       "Founding Partner"),
    (r"^ICAHN CARL",                              "Icahn",                 "Carl Icahn",         "Chairman"),
    (r"^BAUPOST GROUP",                           "Baupost",               "Seth Klarman",       "CEO & Portfolio Manager"),
    (r"^LONE PINE CAPITAL",                       "Lone Pine",             "Stephen Mandel",     "Founder"),
    (r"^VIKING GLOBAL INVESTORS",                 "Viking Global",         "O. Andreas Halvorsen","Co-Founder & CIO"),
    (r"^D1 CAPITAL PARTNERS",                     "D1 Capital",            "Daniel Sundheim",    "Founder & CIO"),
    (r"^RENAISSANCE TECHNOLOGIES",                "Renaissance",           "Peter Brown",        "CEO"),
    (r"^TWO SIGMA INVESTMENTS",                   "Two Sigma",             "Carter Lyons",       "CEO"),
    (r"^AQR CAPITAL MANAGEMENT",                  "AQR",                   "Cliff Asness",       "Founder & CIO"),
    (r"^ALTIMETER CAPITAL MANAGEMENT",            "Altimeter",             "Brad Gerstner",      "Founder & CEO"),
    (r"^MELVIN CAPITAL|^PENTWATER CAPITAL",       "Pentwater",             "Matthew Halbower",   "Founder & CEO"),
    (r"^BAILLIE GIFFORD",                         "Baillie Gifford",       "Baillie Gifford",    "Partnership"),
]

rows = []
with open(LOOKUP, encoding="utf-8", errors="replace") as fh:
    for line in fh:
        line = line.rstrip("\n")
        if not line.endswith(":"):
            continue
        name, cik = line[:-1].rsplit(":", 1)
        rows.append((name, cik))
print(f"loaded {len(rows)} EDGAR entities", file=sys.stderr)

out = {}
for pat, disp, person, title in WANT:
    rx = re.compile(pat)
    cands = [(n, c) for n, c in rows if rx.match(n)]
    chosen = None
    for n, c in cands[:6]:
        s = submissions(c.zfill(10))
        time.sleep(0.12)
        if not s:
            continue
        forms = set(s.get("filings", {}).get("recent", {}).get("form", []))
        score = sum(f.startswith(("13F", "SC 13D", "SC 13G", "SCHEDULE 13")) for f in forms)
        if score:
            chosen = {"cik": c.zfill(10), "edgar_name": n, "display": disp,
                      "person": person, "title": title,
                      "forms": sorted(f for f in forms if f.startswith(("13F", "SC 13", "SCHEDULE 13", "4")))[:6]}
            break
    if chosen:
        out[disp] = chosen
        print(f"OK   {chosen['cik']}  {disp:18s} {person:24s} {chosen['forms']}")
    else:
        print(f"MISS {disp:18s} ({len(cands)} name matches, none file 13F/13D)")

json.dump(out, open(sys.argv[1], "w"), indent=2)
print(f"\nresolved {len(out)}/{len(WANT)}")
