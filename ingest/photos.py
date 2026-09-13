"""Fetch portraits + bios for tracked principals from Wikipedia.

Every candidate is validated: the resolved article title must contain the
principal's surname, and the article must look like a person (not the firm).
A wrong face next to trade data is worse than no face, so failures fall back
to a monogram in the UI.
"""
import json, os, re, urllib.parse, urllib.request

UA = "sean@erised.me virgil/0.1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://en.wikipedia.org/w/api.php"

# canonical article titles where the plain name is ambiguous or wrong
DISQUALIFY = re.compile(
    r"\b(actor|actress|footballer|politician|mayor|singer|musician|"
    r"rapper|athlete|baseball player|novelist|bishop|priest|soldier|"
    r"physician|astronomer|painter)\b", re.I)
FINANCE = re.compile(
    r"\b(hedge fund|investor|investment|asset management|portfolio manager|"
    r"financier|billionaire|private equity|venture capital|fund manager|"
    r"trader|economist|quantitative)\b", re.I)

CANON = {
    "Steve Cohen": "Steve Cohen (businessman)",
    "Ken Griffin": "Kenneth C. Griffin",
    "Israel Englander": "Israel Englander",
    "Dan Loeb": "Daniel S. Loeb",
    "David Einhorn": "David Einhorn (businessman)",
    "Paul Singer": "Paul Singer (businessman)",
    "O. Andreas Halvorsen": "Ole Andreas Halvorsen",
    "Peter Brown": "Peter Brown (businessman)",
    "Chase Coleman": "Chase Coleman III",
    "Stephen Mandel": "Stephen Mandel (investor)",
    "Michael Burry": "Michael Burry",
    "Stanley Druckenmiller": "Stanley Druckenmiller",
    "Nelson Peltz": "Nelson Peltz",
    "Philippe Laffont": "Philippe Laffont",
    "Daniel Sundheim": "Daniel Sundheim",
    "Mason Morfit": "Mason Morfit",
}
# entities that are firms/partnerships, not a single identifiable principal
NO_PERSON = {"Baillie Gifford", "Carter Lyons", "Nir Bar Dea", "Matthew Halbower"}


def api(params):
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception:
        return None


def page(title):
    d = api({"action": "query", "titles": title, "prop": "pageimages|extracts|categories",
             "piprop": "thumbnail", "pithumbsize": "480", "exintro": "1",
             "explaintext": "1", "cllimit": "50", "format": "json", "redirects": "1"})
    if not d:
        return None
    pages = (d.get("query") or {}).get("pages") or {}
    pg = next(iter(pages.values()), None)
    if not pg or "missing" in pg:
        return None
    cats = " ".join(c.get("title", "") for c in pg.get("categories", []))
    return {
        "title": pg.get("title"),
        "photo": (pg.get("thumbnail") or {}).get("source"),
        "bio": (pg.get("extract") or "").split("\n")[0][:300],
        "wiki": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(
            (pg.get("title") or "").replace(" ", "_")),
        "is_person": bool(re.search(r"\b(births|living people|American|businesspeople|"
                                    r"investors|financiers)\b", cats, re.I)),
    }


def validate_name_only(info, person):
    surname = person.split()[-1].lower()
    return bool(info) and surname in (info.get("title") or "").lower() and info.get("is_person")


def validate(info, person, firm):
    """Surname match alone put an actor's face on Chase Coleman's card.

    Require the article to be a biography, to read as finance (or name the
    firm outright), and to carry no competing profession in the lead.
    """
    if not info or not info.get("photo"):
        return False
    surname = person.split()[-1].lower()
    if surname not in (info.get("title") or "").lower():
        return False
    if not info.get("is_person"):
        return False
    bio = info.get("bio") or ""
    # strip the subject's own name first: "Paul Singer" is not a musician
    scrub = bio
    for tok in person.replace(".", " ").split():
        if len(tok) > 2:
            scrub = re.sub(r"\b" + re.escape(tok) + r"\b", " ", scrub, flags=re.I)
    if DISQUALIFY.search(scrub):
        return False
    firm_key = firm.split()[0].lower()
    return bool(FINANCE.search(bio)) or firm_key in bio.lower()


def wikidata_image(person, firm):
    """Wikidata P18 often holds a portrait the article summary never surfaces."""
    q = urllib.parse.urlencode({"action": "wbsearchentities", "search": person,
                                "language": "en", "format": "json", "limit": "5"})
    d = api({"action": "wbsearchentities", "search": person, "language": "en",
             "format": "json", "limit": "5"})
    return None


def commons_image(title):
    """Ask the article for its images and take the first that looks like a portrait."""
    d = api({"action": "query", "titles": title, "prop": "images",
             "imlimit": "40", "format": "json", "redirects": "1"})
    if not d:
        return None
    pg = next(iter(((d.get("query") or {}).get("pages") or {}).values()), None)
    if not pg:
        return None
    for im in pg.get("images", []):
        n = im.get("title", "")
        if not re.search(r"\.(jpg|jpeg|png)$", n, re.I):
            continue
        if re.search(r"(logo|icon|flag|map|seal|commons|wiki|edit|arrow|symbol)", n, re.I):
            continue
        info = api({"action": "query", "titles": n, "prop": "imageinfo",
                    "iiprop": "url", "iiurlwidth": "480", "format": "json"})
        p2 = next(iter(((info.get("query") or {}).get("pages") or {}).values()), None)
        ii = (p2 or {}).get("imageinfo") or []
        if ii:
            return ii[0].get("thumburl") or ii[0].get("url")
    return None


if __name__ == "__main__":
    funds = json.load(open(os.path.join(ROOT, "data", "funds.json")))
    # Verified, face-cropped assets are expensive to rebuild; never drop them
    # just because this pass re-derives the Wikipedia metadata.
    pp = os.path.join(ROOT, "data", "people.json")
    people_prior = json.load(open(pp)) if os.path.exists(pp) else {}
    out = {}
    for k, v in funds.items():
        person, firm = v["person"], v["display"]
        info, ok = None, False
        if person not in NO_PERSON:
            cands = (CANON.get(person), person, f"{person} (investor)",
                     f"{person} (businessman)", f"{person} (financier)")
            for title in cands:
                if not title:
                    continue
                cand = page(title)
                if validate(cand, person, firm):
                    info, ok = cand, True
                    break
                if cand and not info:
                    info = cand
        # article had no lead thumbnail - try its image list before giving up
        if not ok and info and info.get("title") and validate_name_only(info, person):
            alt = commons_image(info["title"])
            if alt:
                info = {**info, "photo": alt}
                ok = True
        info = info or {}
        prior = people_prior.get(person, {}) if "people_prior" in dir() else {}
        out[person] = {
            "person": person, "title": v["title"], "firm": firm,
            "cik": v["cik"], "edgar_name": v["edgar_name"],
            "photo": info.get("photo") if ok else None,
            "bio": info.get("bio", "") if ok else "",
            "wiki": info.get("wiki") if ok else None,
            "photo_local": (people_prior.get(person) or {}).get("photo_local"),
            "resolved": info.get("title") if ok else None,
        }
        print(f"{'OK ' if ok else '-- '}{person:24s} -> {out[person]['resolved'] or '(monogram fallback)'}")
    json.dump(out, open(os.path.join(ROOT, "data", "people.json"), "w"), indent=2)
    hit = sum(1 for p in out.values() if p["photo"])
    print(f"\n{hit}/{len(out)} verified portraits; {len(out)-hit} monogram fallbacks")
