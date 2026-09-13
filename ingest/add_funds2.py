"""Second wave of tracked managers: more hedge funds, sovereign wealth, and the
large asset managers whose 13Fs move markets.

Note on private equity: most PE firms file no 13F at all, because their book is
private companies rather than 13(f) securities. Vista, Thoma Bravo, Silver Lake
and the rest simply do not appear — the same reason KKR's only filer is its
credit arm. Adding them would produce empty rows, so this list sticks to
entities that actually report holdings.
"""
import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

WANT = [
    # --- macro / multistrat ---
    (r"^BREVAN HOWARD",              "Brevan Howard",    "Alan Howard",      "Founder"),
    (r"^CAXTON ASSOCIATES",          "Caxton",           "Andrew Law",       "Chairman & CIO"),
    (r"^MOORE CAPITAL MANAGEMENT",   "Moore Capital",    "Louis Bacon",      "Founder"),
    (r"^TUDOR INVESTMENT",           "Tudor",            "Paul Tudor Jones", "Founder"),
    (r"^ELEMENT CAPITAL MANAGEMENT", "Element Capital",  "Jeffrey Talpins",  "Founder & CIO"),
    (r"^ROKOS CAPITAL",              "Rokos",            "Chris Rokos",      "Founder & CIO"),
    (r"^CAPULA",                     "Capula",           "Yan Huo",          "Founder & CIO"),
    (r"^VERITION FUND MANAGEMENT",   "Verition",         "Nicholas Maounis", "Founder"),
    (r"^WALLEYE",                    "Walleye",          "Will England",     "CEO & CIO"),
    (r"^SQUAREPOINT",                "Squarepoint",      "Olivier Durantel", "Co-Founder"),
    (r"^QUBE RESEARCH",              "Qube Research",    "Pierre-Yves Morlat","Co-Founder"),
    (r"^WINTON",                     "Winton",           "David Harding",    "Founder & CEO"),
    (r"^PDT PARTNERS",               "PDT Partners",     "Peter Muller",     "Founder & CEO"),
    (r"^VOLEON",                     "Voleon",           "Michael Kharitonov","Co-Founder"),
    # --- equity long/short ---
    (r"^EMINENCE CAPITAL",           "Eminence",         "Ricky Sandler",    "Founder & CIO"),
    (r"^STEADFAST CAPITAL MANAGEMENT","Steadfast",       "Robert Pitts",     "Founder"),
    (r"^ALKEON CAPITAL",             "Alkeon",           "Panayotis Sparaggis","Founder & CIO"),
    (r"^HITCHWOOD CAPITAL",          "Hitchwood",        "James Crichton",   "Founder"),
    (r"^TYBOURNE CAPITAL",           "Tybourne",         "Eashwar Krishnan", "Founder"),
    (r"^SANDS CAPITAL",              "Sands Capital",    "Frank Sands",      "CEO & CIO"),
    (r"^BARON CAPITAL",              "Baron Capital",    "Ron Baron",        "Founder & CEO"),
    (r"^ARTISAN PARTNERS",           "Artisan Partners", "Eric Colson",      "CEO"),
    (r"^HARRIS ASSOCIATES",          "Harris (Oakmark)", "Bill Nygren",      "Portfolio Manager"),
    (r"^DODGE & COX",                "Dodge & Cox",      "Dana Emery",       "Chair & CEO"),
    (r"^FIRST EAGLE INVESTMENT",     "First Eagle",      "Mehdi Mahmud",     "CEO"),
    (r"^SOUTHEASTERN ASSET",         "Southeastern",     "Mason Hawkins",    "Founder"),
    (r"^ARIEL INVESTMENTS",          "Ariel",            "John Rogers Jr.",  "Founder & Co-CEO"),
    (r"^DIAMOND HILL",               "Diamond Hill",     "Heather Brilliant","CEO"),
    (r"^PZENA INVESTMENT",           "Pzena",            "Richard Pzena",    "Founder"),
    (r"^GQG PARTNERS",               "GQG Partners",     "Rajiv Jain",       "Chairman & CIO"),
    (r"^LINDSELL TRAIN",             "Lindsell Train",   "Nick Train",       "Co-Founder"),
    (r"^SELECT EQUITY GROUP",        "Select Equity",    "George Loening",   "Founder"),
    # --- credit / distressed / event ---
    (r"^KING STREET CAPITAL",        "King Street",      "Brian Higgins",    "Co-Founder"),
    (r"^SILVER POINT CAPITAL",       "Silver Point",     "Edward Mule",      "Co-Founder"),
    (r"^ANCHORAGE CAPITAL",          "Anchorage",        "Kevin Ulrich",     "CEO"),
    (r"^KNIGHTHEAD CAPITAL",         "Knighthead",       "Tom Wagner",       "Co-Founder"),
    (r"^DIAMETER CAPITAL",           "Diameter",         "Scott Goodwin",    "Co-Founder"),
    (r"^MARATHON ASSET MANAGEMENT",  "Marathon AM",      "Bruce Richards",   "CEO"),
    (r"^OAKTREE CAPITAL MANAGEMENT", "Oaktree",          "Howard Marks",     "Co-Chairman"),
    (r"^ANGELO,? GORDON",            "Angelo Gordon",    "Josh Baumgarten",  "Co-CEO"),
    (r"^SIXTH STREET",               "Sixth Street",     "Alan Waxman",      "CEO"),
    # --- sovereign wealth / pensions ---
    (r"^NORGES BANK",                "Norges Bank",      "Nicolai Tangen",   "CEO"),
    (r"^GIC PRIVATE",                "GIC",              "Lim Chow Kiat",    "CEO"),
    (r"^TEMASEK",                    "Temasek",          "Dilhan Pillay",    "CEO"),
    (r"^CANADA PENSION PLAN",        "CPP Investments",  "John Graham",      "CEO"),
    (r"^CAISSE DE DEPOT",            "CDPQ",             "Charles Emond",    "CEO"),
    (r"^ONTARIO TEACHERS",           "Ontario Teachers'","Jo Taylor",        "CEO"),
    (r"^CALIFORNIA PUBLIC EMPLOYEES","CalPERS",          "Marcie Frost",     "CEO"),
    (r"^ALBERTA INVESTMENT",         "AIMCo",            "Ray Gilmour",      "Interim CEO"),
    # --- large asset managers ---
    (r"^JPMORGAN CHASE & CO",        "JPMorgan AM",      "Mary Erdoes",      "CEO, Asset & Wealth"),
    (r"^MORGAN STANLEY$",            "Morgan Stanley IM","Ted Pick",         "CEO"),
    (r"^UBS GROUP AG",               "UBS",              "Sergio Ermotti",   "CEO"),
    (r"^AMUNDI",                     "Amundi",           "Valerie Baudson",  "CEO"),
    (r"^SCHRODERS",                  "Schroders",        "Richard Oldfield", "CEO"),
    (r"^INVESCO LTD",                "Invesco",          "Andrew Schlossberg","CEO"),
    (r"^FRANKLIN RESOURCES",         "Franklin Templeton","Jenny Johnson",   "CEO"),
    (r"^NUVEEN",                     "Nuveen",           "Jose Minaya",      "CEO"),
    (r"^NEUBERGER BERMAN",           "Neuberger Berman", "George Walker",    "CEO"),
    (r"^MASSACHUSETTS FINANCIAL SERVICES", "MFS",        "Mike Roberge",     "CEO"),
    (r"^JANUS HENDERSON",            "Janus Henderson",  "Ali Dibadj",       "CEO"),
    (r"^ALLIANCEBERNSTEIN",          "AllianceBernstein","Onur Erzan",       "CEO"),
    (r"^LEGAL & GENERAL",            "Legal & General",  "Antonio Simoes",   "CEO"),
    (r"^BAILLIE GIFFORD",            "Baillie Gifford",  "Baillie Gifford",  "Partnership"),
    # --- market makers ---
    (r"^VIRTU FINANCIAL",            "Virtu",            "Doug Cifu",        "CEO"),
    (r"^HRT FINANCIAL|^HUDSON RIVER TRADING", "Hudson River Trading", "Jason Carroll", "Co-Founder"),
    (r"^JUMP FINANCIAL|^JUMP TRADING", "Jump Trading",   "Bill DiSomma",     "Co-Founder"),
    (r"^DRW",                        "DRW",              "Don Wilson",       "Founder & CEO"),
    (r"^OPTIVER",                    "Optiver",          "Optiver",          "Partnership"),
    (r"^IMC ",                       "IMC",              "IMC",              "Partnership"),
    (r"^FLOW TRADERS",               "Flow Traders",     "Mike Kuehnel",     "CEO"),
    (r"^TOWER RESEARCH",             "Tower Research",   "Mark Gorton",      "Founder"),
    (r"^QUANTLAB",                   "Quantlab",         "Wilbur Matthews",  "Co-Founder"),
    (r"^GTS SECURITIES|^GLOBAL TRADING SYSTEMS", "GTS",  "Ari Rubenstein",   "Co-Founder"),
]


def load_rows():
    rows = []
    with open(os.path.join(DATA, "cik-lookup.txt"), encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.endswith(":"):
                continue
            name, cik = line[:-1].rsplit(":", 1)
            rows.append((name, cik.zfill(10)))
    return rows


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
    rows = load_rows()
    funds = json.load(open(os.path.join(DATA, "funds.json")))
    have = {v["cik"] for v in funds.values()}
    added, missed = 0, []
    for pat, disp, person, title in WANT:
        if disp in funds:
            continue
        rx = re.compile(pat, re.I)
        cands = sorted({(n, c) for n, c in rows if rx.match(n)}, key=lambda x: len(x[0]))[:10]
        best = None
        for n, c in cands:
            if c in have:
                continue
            period, nm = latest_13f(c)
            if period and (best is None or period > best[0]):
                best = (period, c, nm)
        if best:
            funds[disp] = {"cik": best[1], "edgar_name": best[2], "display": disp,
                           "person": person, "title": title, "forms": ["13F-HR"]}
            have.add(best[1])
            added += 1
            print(f"OK   {best[1]}  {disp:20s} {person:22s} {best[0]}  {best[2][:38]}")
        else:
            missed.append(disp)
    json.dump(funds, open(os.path.join(DATA, "funds.json"), "w"), indent=2)
    print(f"\nadded {added}; funds.json now holds {len(funds)}")
    print(f"no 13F filer found for {len(missed)}: {', '.join(missed)}")
