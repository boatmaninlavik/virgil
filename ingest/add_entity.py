"""Ingest investors the user asked for from the page.

ui/serve.py appends {cik, name} to data/requests.json when someone picks a
filer in the "add this investor" flow. This turns that into a tracked entity:
confirm on EDGAR what they actually file, add them to the watchlist so the
60-second poller sees their Form 4s, 13Ds and 144s from now on, and pull their
13F history so they have holdings the moment they appear.

An entity that turns out to file nothing we can use is marked and reported
rather than silently dropped, because "this person has no filings" is a real
answer the page should be able to give.

    python3 ingest/add_entity.py
"""
import html, json, os, re, subprocess, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edgar
import wikidata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
QUEUE = os.path.join(DATA, "requests.json")
FUNDS = os.path.join(DATA, "funds.json")
USEFUL = {"13F-HR", "13F-HR/A", "4", "4/A", "3", "5", "144",
          "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}


def load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def save(path, obj, indent=1):
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w"), indent=indent)
    os.replace(tmp, path)


def profile(cik):
    """What does this CIK actually file? (edgar_name, forms, last_filed)"""
    try:
        sub = edgar.submissions(cik)      # already decoded
    except Exception:
        return None, set(), ""
    name = sub.get("name") or ""
    recent = (sub.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    have = {f for f in forms if f in USEFUL}
    return name, have, (max(dates) if dates else "")


AGGREGATOR = re.compile(
    r"(wikipedia|wikidata|bloomberg|linkedin|sec\.gov|crunchbase|pitchbook|"
    r"reuters|forbes|marketscreener|zoominfo|dnb\.com|facebook|twitter|x\.com|"
    r"youtube|glassdoor|indeed|whalewisdom|insidermonkey|hedgefollow|stockzoa)", re.I)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}


LEGAL = {"llc", "lp", "l", "p", "inc", "ltd", "co", "corp", "company", "the",
         "limited", "partners", "partnership", "holdings", "group", "llp", "plc",
         "sa", "nv", "ag", "gmbh", "trust", "fund", "funds"}


def guess_domain(name, verbose=False):
    """The firm's own website, for logo scraping.

    Wikidata's P856 answers for well-documented firms and nothing for the rest.
    A web-search fallback proved worthless - Bing returns the ARK video game for
    "ARK Investment Management" no matter how the query is phrased - so instead
    build the handful of domains a firm of this name would plausibly own and
    check which one actually answers and mentions the firm.
    """
    try:
        host = wikidata.website(name, (name + " LLC", name + " LP"))
        if host:
            return host
    except Exception:
        pass

    toks = [t for t in re.split(r"[^A-Za-z0-9]+", name.lower())
            if t and t not in LEGAL]
    if not toks:
        return None
    short = [t[:6] if len(t) > 7 else t for t in toks]
    stems = []
    for parts in ([toks], [toks[:2]], [toks[:1]], [short[:2]], [[toks[0], short[1]]]
                  if len(toks) > 1 else []):
        for joiner in ("", "-"):
            stems.append(joiner.join(parts[0]))
    seen, cands = set(), []
    for st in stems:
        if len(st) < 3 or st in seen:
            continue
        seen.add(st)
        cands += [st + ".com", st + "capital.com"]
    probe = toks[0]
    for d in cands[:12]:
        try:
            r = urllib.request.urlopen(
                urllib.request.Request("https://" + d, headers=UA), timeout=7)
            body = r.read(120_000).decode("utf-8", "replace").lower()
        except Exception:
            continue
        # Answering is not enough - a squatter answers too, and "ark" alone
        # matched arkinvestcapital.com, an unrelated firm. Require the firm's
        # actual name to appear as a phrase in the page text.
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
        phrase = " ".join(toks[:2]) if len(toks) > 1 else probe
        loose = phrase.replace(" ", "")
        if phrase not in text and loose not in text.replace(" ", ""):
            continue
        if verbose:
            print(f"    domain: {d}", flush=True)
        return d
    return None


def enrich(label, rec, person=""):
    """Give a newly added fund what every other fund already has.

    Holdings alone render nothing: the page builds profile cards from
    people.json and tiles from fund_logos.json, so an entity with neither shows
    up as an empty row. Fetch the mark and the principal's photo here rather
    than leaving the user to wonder why their fund looks broken.
    """
    out = {}
    name = rec.get("edgar_name") or label
    domain = guess_domain(name)
    if domain:
        out["domain"] = domain
        try:
            import site_logos
            path, chip = site_logos.best(domain, site_logos.slugify(label), name)
            if path:
                fl = json.load(open(os.path.join(DATA, "fund_logos.json")))
                fl[label] = {"domain": domain, "logo_local": path,
                             "sharp": True, "chip": chip}
                save(os.path.join(DATA, "fund_logos.json"), fl)
                out["logo"] = path
        except Exception as e:
            out["logo_error"] = type(e).__name__

    people = json.load(open(os.path.join(DATA, "people.json")))
    key = person.title() if person else label
    entry = people.get(key, {})
    entry.update({"person": key, "firm": label, "cik": rec["cik"],
                  "edgar_name": name, "title": entry.get("title") or
                  ("Principal" if person else "Fund"), "user_added": True})
    if not person:
        entry["is_firm"] = True
    people[key] = entry
    save(os.path.join(DATA, "people.json"), people, indent=2)
    out["profile"] = key

    if person:
        try:
            import imagesearch
            slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")
            local, src, _ = imagesearch.find(key, label, slug)
            if local:
                people = json.load(open(os.path.join(DATA, "people.json")))
                people[key]["photo_local"] = local
                people[key]["photo_source"] = src
                save(os.path.join(DATA, "people.json"), people, indent=2)
                out["photo"] = local
        except Exception as e:
            out["photo_error"] = type(e).__name__
    return out


def add(req, funds):
    cik = req["cik"]
    name, forms, last = profile(cik)
    if not name:
        return "unreachable", f"EDGAR returned nothing for CIK {cik}"
    if not forms:
        return "no-filings", (f"{name} has no 13F, 13D/G or Form 4 on record — "
                              f"nothing public to track")
    label = req.get("label") or name
    funds[label] = {
        "cik": cik,
        "edgar_name": name,
        "display": label,
        "person": (req.get("person") or "").title(),
        "title": "Principal" if req.get("person") else "",
        "forms": sorted(forms),
        "added": req.get("requested") or time.strftime("%Y-%m-%d"),
        "user_added": True,
    }
    return "added", f"{name} — files {', '.join(sorted(forms))[:60]}, last {last}"


if __name__ == "__main__":
    q = load(QUEUE, [])
    pending = [r for r in q if r.get("state") == "pending"]
    if not pending:
        print("no pending requests")
        sys.exit(0)

    funds = load(FUNDS, {})
    did13f = False
    for r in pending:
        state, msg = add(r, funds)
        r["state"] = state
        r["note"] = msg
        r["resolved"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(f"  {state:12s} {msg}", flush=True)
        if state == "added":
            label = r.get("label") or ""
            extra = enrich(label, funds[label], r.get("person") or "")
            r["enriched"] = extra
            print(f"               logo={extra.get('logo','-')} "
                  f"profile={extra.get('profile','-')} "
                  f"photo={extra.get('photo','-')}", flush=True)
            if any(f.startswith("13F") for f in funds[label]["forms"]):
                did13f = True

    save(FUNDS, funds)
    save(QUEUE, q)

    # Holdings and history only matter for 13F filers, and both are slow, so
    # run them once after the batch rather than per request.
    if did13f:
        for script in ("holdings.py", "history.py"):
            path = os.path.join(ROOT, "ingest", script)
            if os.path.exists(path):
                subprocess.run([sys.executable, path], cwd=ROOT,
                               capture_output=True, timeout=1800)
        print("  holdings + history refreshed")
    print(f"\n{sum(1 for r in pending if r['state']=='added')} added, "
          f"{sum(1 for r in pending if r['state']=='no-filings')} with no filings")
