"""Expand the tracked-fund universe and verify each entity actually files 13F-HR.

Name-matching against EDGAR's CIK table is not enough: many groups register
dozens of vehicles and only one of them files the holdings report.
"""
import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LOOKUP = os.path.join(DATA, "cik-lookup.txt")

# (regex against EDGAR name, display, principal, title)
WANT = [
    # --- public alternative managers: they file 13F like anyone else ---
    (r"^BLACKSTONE INC",                 "Blackstone",        "Stephen Schwarzman", "Chairman & CEO"),
    (r"^KKR & CO\. INC",                 "KKR",               "Joe Bae & Scott Nuttall", "Co-CEOs"),
    (r"^APOLLO GLOBAL MANAGEMENT, INC",  "Apollo",            "Marc Rowan",       "CEO"),
    (r"^CARLYLE GROUP INC",              "Carlyle",           "Harvey Schwartz",  "CEO"),
    (r"^ARES MANAGEMENT CORP",           "Ares",              "Michael Arougheti","CEO"),
    (r"^BLUE OWL CAPITAL INC",           "Blue Owl",          "Doug Ostrover",    "CEO"),
    (r"^TPG INC",                        "TPG",               "Jon Winkelried",   "CEO"),
    (r"^BROOKFIELD CORP",                "Brookfield",        "Bruce Flatt",      "CEO"),
    (r"^BLACKROCK, INC|^BLACKROCK INC",  "BlackRock",         "Larry Fink",       "Chairman & CEO"),
    (r"^VANGUARD GROUP INC",             "Vanguard",          "Salim Ramji",      "CEO"),
    (r"^STATE STREET CORP",              "State Street",      "Ron O'Hanley",     "Chairman & CEO"),
    (r"^FMR LLC",                        "Fidelity (FMR)",    "Abigail Johnson",  "Chairman & CEO"),
    (r"^PRICE T ROWE ASSOCIATES",        "T. Rowe Price",     "Rob Sharps",       "CEO"),
    (r"^WELLINGTON MANAGEMENT GROUP",    "Wellington",        "Jean Hynes",       "CEO"),
    (r"^GEODE CAPITAL MANAGEMENT",       "Geode",             "Vincent Gubitosi", "President"),
    (r"^CAPITAL RESEARCH GLOBAL INVESTORS", "Capital Group",  "Mike Gitlin",      "CEO"),
    # --- multistrategy / quant ---
    (r"^D\. E\. SHAW|^D E SHAW",         "D. E. Shaw",        "David Shaw",       "Founder"),
    (r"^BALYASNY ASSET MANAGEMENT",      "Balyasny",          "Dmitry Balyasny",  "Founder & CEO"),
    (r"^EXODUSPOINT CAPITAL MANAGEMENT", "ExodusPoint",       "Michael Gelband",  "Founder & CEO"),
    (r"^SCHONFELD STRATEGIC ADVISORS",   "Schonfeld",         "Steven Schonfeld", "Founder"),
    (r"^HUDSON BAY CAPITAL MANAGEMENT",  "Hudson Bay",        "Sander Gerber",    "CEO & CIO"),
    (r"^MARSHALL WACE",                  "Marshall Wace",     "Paul Marshall",    "Co-Founder & CIO"),
    (r"^MAN GROUP",                      "Man Group",         "Robyn Grew",       "CEO"),
    (r"^ARROWSTREET CAPITAL",            "Arrowstreet",       "Peter Rathjens",   "Co-Founder"),
    (r"^ACADIAN ASSET MANAGEMENT",       "Acadian",           "Kelly Young",      "CEO"),
    (r"^SUSQUEHANNA INTERNATIONAL GROUP","Susquehanna",       "Jeff Yass",        "Co-Founder"),
    (r"^JANE STREET GROUP",              "Jane Street",       "Jane Street",      "Partnership"),
    # --- fundamental / activist ---
    (r"^FARALLON CAPITAL MANAGEMENT",    "Farallon",          "Andrew Spokes",    "Managing Partner"),
    (r"^DAVIDSON KEMPNER CAPITAL MANAGEMENT", "Davidson Kempner", "Anthony Yoseloff", "Executive Managing Member"),
    (r"^SCULPTOR CAPITAL",               "Sculptor",          "Jimmy Levin",      "CIO"),
    (r"^MAVERICK CAPITAL",               "Maverick",          "Lee Ainslie",      "Founder"),
    (r"^WHALE ROCK CAPITAL MANAGEMENT",  "Whale Rock",        "Alex Sacerdote",   "Founder"),
    (r"^DRAGONEER INVESTMENT GROUP",     "Dragoneer",         "Marc Stad",        "Founder"),
    (r"^DURABLE CAPITAL PARTNERS",       "Durable Capital",   "Henry Ellenbogen", "Founder & CIO"),
    (r"^LIGHT STREET CAPITAL MANAGEMENT","Light Street",      "Glen Kacher",      "Founder"),
    (r"^EGERTON CAPITAL",                "Egerton",           "John Armitage",    "Co-Founder & CIO"),
    (r"^TCI FUND MANAGEMENT",            "TCI",               "Chris Hohn",       "Founder"),
    (r"^GLENVIEW CAPITAL MANAGEMENT",    "Glenview",          "Larry Robbins",    "Founder & CEO"),
    (r"^STARBOARD VALUE",                "Starboard Value",   "Jeff Smith",       "CEO & CIO"),
    (r"^JANA PARTNERS",                  "JANA Partners",     "Barry Rosenstein", "Founder"),
    (r"^SACHEM HEAD CAPITAL MANAGEMENT", "Sachem Head",       "Scott Ferguson",   "Founder"),
    (r"^CORVEX MANAGEMENT",              "Corvex",            "Keith Meister",    "Founder"),
    (r"^AKRE CAPITAL MANAGEMENT",        "Akre Capital",      "John Neff",        "Portfolio Manager"),
    (r"^FUNDSMITH",                      "Fundsmith",         "Terry Smith",      "Founder & CIO"),
    (r"^POLEN CAPITAL MANAGEMENT",       "Polen Capital",     "Stan Moss",        "CEO"),
    (r"^HEALTHCOR MANAGEMENT",           "HealthCor",         "Arthur Cohen",     "Co-Founder"),
    (r"^SLATE PATH CAPITAL",             "Slate Path",        "David Greenspan",  "Founder"),
    (r"^HIMALAYA CAPITAL",               "Himalaya Capital",  "Li Lu",            "Founder & Chairman"),
]


def load_rows():
    rows = []
    with open(LOOKUP, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.endswith(":"):
                continue
            name, cik = line[:-1].rsplit(":", 1)
            rows.append((name, cik.zfill(10)))
    return rows


def files_13f(cik):
    try:
        d = edgar.submissions(cik)
    except Exception:
        return None
    forms = set(d.get("filings", {}).get("recent", {}).get("form", []))
    if "13F-HR" not in forms:
        return None
    return d.get("name")


if __name__ == "__main__":
    rows = load_rows()
    funds = json.load(open(os.path.join(DATA, "funds.json")))
    have = {v["cik"] for v in funds.values()}
    added, missed = 0, []

    for pat, disp, person, title in WANT:
        if disp in funds:
            continue
        rx = re.compile(pat, re.I)
        cands = [(n, c) for n, c in rows if rx.match(n)]
        # prefer the shortest name: the management entity, not its 40 sub-funds
        cands.sort(key=lambda nc: len(nc[0]))
        chosen = None
        for n, c in cands[:8]:
            if c in have:
                continue
            nm = files_13f(c)
            if nm:
                chosen = (c, nm)
                break
        if chosen:
            funds[disp] = {"cik": chosen[0], "edgar_name": chosen[1], "display": disp,
                           "person": person, "title": title, "forms": ["13F-HR"]}
            have.add(chosen[0])
            added += 1
            print(f"OK   {chosen[0]}  {disp:20s} {person:26s} {chosen[1][:44]}")
        else:
            missed.append(disp)
            print(f"MISS {disp:20s} ({len(cands)} name matches, none file 13F-HR)")

    json.dump(funds, open(os.path.join(DATA, "funds.json"), "w"), indent=2)
    print(f"\nadded {added}; funds.json now holds {len(funds)}")
    if missed:
        print("not found:", ", ".join(missed))
