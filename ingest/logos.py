"""Logos for tracked firms, and portrait crop hints for principals.

Logos come from Google's favicon service (free, keyless, 128px). Clearbit's
logo API is gone as of 2024.

The crop hint exists because Wikimedia's lead image is often a full-body shot —
Cliff Asness's article picture is a standing photo, so a centred square crop
lands on his torso. Storing each image's aspect ratio lets the page bias the
crop toward the head on tall images and leave square ones alone.
"""
import json, os, sys, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = "sean@erised.me virgil/0.1"

DOMAINS = {
    "Blackstone": "blackstone.com", "KKR": "kkr.com", "KKR (Credit)": "kkr.com",
    "Apollo": "apollo.com", "Carlyle": "carlyle.com", "Ares": "aresmgmt.com",
    "Blue Owl": "blueowl.com", "TPG": "tpg.com", "Brookfield": "brookfield.com",
    "BlackRock": "blackrock.com", "Vanguard": "vanguard.com",
    "Vanguard Advisers": "vanguard.com", "State Street": "statestreet.com",
    "Fidelity (FMR)": "fidelity.com", "T. Rowe Price": "troweprice.com",
    "Wellington": "wellington.com", "Geode": "geodecapital.com",
    "Capital Group": "capitalgroup.com", "Berkshire Hathaway": "berkshirehathaway.com",
    "Pershing Square": "pershingsquareholdings.com", "Point72": "point72.com",
    "Citadel": "citadel.com", "Millennium": "mlp.com", "Bridgewater": "bridgewater.com",
    "Elliott": "elliottmgmt.com", "Third Point": "thirdpoint.com",
    "Greenlight Capital": "greenlightcapital.com", "Appaloosa": "appaloosamanagement.com",
    "Scion": "scionasset.com", "Tiger Global": "tigerglobal.com",
    "Coatue": "coatue.com", "Duquesne": "duquesnefamilyoffice.com",
    "Soros Fund Mgmt": "soros.com", "ValueAct": "valueact.com",
    "Trian": "trianpartners.com", "Icahn": "ielp.com", "Baupost": "baupost.com",
    "Lone Pine": "lonepinecapital.com", "Viking Global": "vikingglobal.com",
    "D1 Capital": "d1capital.com", "Renaissance": "rentec.com",
    "Two Sigma": "twosigma.com", "AQR": "aqr.com", "Altimeter": "altimeter.com",
    "Pentwater": "pentwater.com", "Baillie Gifford": "bailliegifford.com",
    "D. E. Shaw": "deshaw.com", "Balyasny": "bamfunds.com",
    "ExodusPoint": "exoduspoint.com", "Schonfeld": "schonfeld.com",
    "Hudson Bay": "hudsonbaycapital.com", "Marshall Wace": "mwam.com",
    "Man Group": "man.com", "Arrowstreet": "arrowstreetcapital.com",
    "Acadian": "acadian-asset.com", "Susquehanna": "sig.com",
    "Jane Street": "janestreet.com", "Farallon": "faralloncapital.com",
    "Davidson Kempner": "dkp.com", "Sculptor": "sculptor.com",
    "Maverick": "maverickcap.com", "Whale Rock": "whalerock.com",
    "Dragoneer": "dragoneerinvest.com", "Durable Capital": "durablecapital.com",
    "Light Street": "lightstreetcap.com", "Egerton": "egertoncapital.com",
    "TCI": "tcifund.com", "Glenview": "glenviewcapital.com",
    "Starboard Value": "starboardvalue.com", "JANA Partners": "janapartners.com",
    "Sachem Head": "sachemhead.com", "Corvex": "corvexcap.com",
    "Akre Capital": "akrecapital.com", "Fundsmith": "fundsmith.co.uk",
    "Polen Capital": "polencapital.com", "HealthCor": "healthcormanagement.com",
    "Slate Path": "slatepath.com", "Himalaya Capital": "himalayacapital.com",
    "Caxton": "caxton.com",
    "Moore Capital": "moorecap.com",
    "Tudor": "tudor.com",
    "Element Capital": "elementcapital.com",
    "Rokos": "rokoscapital.com",
    "Capula": "capulaglobal.com",
    "Verition": "verition.com",
    "Walleye": "walleyecapital.com",
    "Squarepoint": "squarepoint-capital.com",
    "Qube Research": "qube-rt.com",
    "Winton": "winton.com",
    "PDT Partners": "pdtpartners.com",
    "Voleon": "voleon.com",
    "Eminence": "eminencecapital.com",
    "Steadfast": "steadfastcapital.com",
    "Alkeon": "alkeon.com",
    "Hitchwood": "hitchwoodcapital.com",
    "Tybourne": "tybournecapital.com",
    "Sands Capital": "sandscapital.com",
    "Artisan Partners": "artisanpartners.com",
    "Baron Capital": "baronfunds.com",
    "Harris (Oakmark)": "oakmark.com",
    "Dodge & Cox": "dodgeandcox.com",
    "First Eagle": "firsteagle.com",
    "Southeastern": "southeasternasset.com",
    "Ariel": "arielinvestments.com",
    "Diamond Hill": "diamond-hill.com",
    "Pzena": "pzena.com",
    "GQG Partners": "gqg.com",
    "Lindsell Train": "lindselltrain.com",
    "Select Equity": "selectequity.com",
    "King Street": "kingstreet.com",
    "Silver Point": "silverpointcapital.com",
    "Anchorage": "anchoragecapital.com",
    "Knighthead": "knightheadcapital.com",
    "Diameter": "diameter.com",
    "Marathon AM": "marathonfund.com",
    "Oaktree": "oaktreecapital.com",
    "Angelo Gordon": "angelogordon.com",
    "Sixth Street": "sixthstreet.com",
    "Norges Bank": "nbim.no",
    "GIC": "gic.com.sg",
    "Temasek": "temasek.com.sg",
    "CPP Investments": "cppinvestments.com",
    "CDPQ": "cdpq.com",
    "Ontario Teachers'": "otpp.com",
    "CalPERS": "calpers.ca.gov",
    "AIMCo": "aimco.ca",
    "JPMorgan AM": "jpmorgan.com",
    "Morgan Stanley IM": "morganstanley.com",
    "UBS": "ubs.com",
    "Amundi": "amundi.com",
    "Schroders": "schroders.com",
    "Invesco": "invesco.com",
    "Franklin Templeton": "franklinresources.com",
    "Nuveen": "nuveen.com",
    "Neuberger Berman": "nb.com",
    "MFS": "mfs.com",
    "Janus Henderson": "janushenderson.com",
    "AllianceBernstein": "alliancebernstein.com",
    "Legal & General": "legalandgeneral.com",
    "Virtu": "virtu.com",
    "Hudson River Trading": "hudsonrivertrading.com",
    "Jump Trading": "jumptrading.com",
    "DRW": "drw.com",
    "Optiver": "optiver.com",
    "IMC": "imc.com",
    "Flow Traders": "flowtraders.com",
    "Tower Research": "tower-research.com",
    "Quantlab": "quantlab.com",
    "GTS": "gtsx.com",
}
FIRM_TICKERS = {
    "BX": "blackstone.com", "KKR": "kkr.com", "APO": "apollo.com",
    "CG": "carlyle.com", "ARES": "aresmgmt.com", "OWL": "blueowl.com",
    "TPG": "tpg.com", "BN": "brookfield.com", "GS": "goldmansachs.com",
    "MS": "morganstanley.com", "EVR": "evercore.com", "LAZ": "lazard.com",
    "PJT": "pjtpartners.com", "MC": "moelis.com", "HLI": "hl.com",
    "PWP": "pwpartners.com", "JEF": "jefferies.com", "BLK": "blackrock.com",
    "SCHW": "schwab.com", "RJF": "raymondjames.com",
}


def logo(domain, size=128):
    return f"https://www.google.com/s2/favicons?domain={domain}&sz={size}"


def image_shape(url):
    """Wikimedia file dimensions, so the page can crop tall photos to the head."""
    if not url or "wikipedia.org" not in url and "wikimedia.org" not in url:
        return None
    # strip the tracking query string before taking the filename, and drop the
    # NNNpx- prefix that thumbnail URLs carry
    fn = urllib.parse.unquote(url.split("?", 1)[0].rsplit("/", 1)[-1])
    if "px-" in fn:
        head, _, rest = fn.partition("px-")
        if head.rsplit("-", 1)[-1].isdigit() or head.isdigit():
            fn = rest
    q = urllib.parse.urlencode({"action": "query", "titles": "File:" + fn,
                                "prop": "imageinfo", "iiprop": "size",
                                "format": "json"})
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(
            "https://en.wikipedia.org/w/api.php?" + q,
            headers={"User-Agent": UA}), timeout=20).read())
    except Exception:
        return None
    pg = next(iter(((d.get("query") or {}).get("pages") or {}).values()), None)
    ii = (pg or {}).get("imageinfo") or []
    if not ii:
        return None
    w, h = ii[0].get("width"), ii[0].get("height")
    if not w or not h:
        return None
    ratio = h / w
    # tall frame -> full-body or 3/4 shot, bias the crop upward toward the face
    pos = 8 if ratio >= 1.6 else 14 if ratio >= 1.3 else 25
    return {"w": w, "h": h, "ratio": round(ratio, 2), "focus": pos}


if __name__ == "__main__":
    people = json.load(open(os.path.join(DATA, "people.json")))
    funds = json.load(open(os.path.join(DATA, "funds.json")))
    n = 0
    for person, p in people.items():
        firm = p.get("firm")
        p["domain"] = DOMAINS.get(firm)
        if p.get("photo"):
            sh = image_shape(p["photo"])
            if sh:
                p["shape"] = sh
                n += 1
                print(f"  {person:24s} {sh['w']}x{sh['h']} ratio {sh['ratio']:.2f} -> focus {sh['focus']}%")
    json.dump(people, open(os.path.join(DATA, "people.json"), "w"), indent=2)

    # firm_logos.json / fund_logos.json belong to brand.py, which resolves real
    # high-resolution marks. Writing plain favicons here silently undid that.
    print(f"\n{n} portraits measured; "
          f"{sum(1 for p in people.values() if p.get('logo'))} fund logo hints "
          f"(authoritative logos come from brand.py)")
