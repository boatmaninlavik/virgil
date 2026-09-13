"""EDGAR client + filing parsers for the copy-trade feed.

Two distinct Form 4 roles matter here and must not be conflated:
  * issuer == a watched firm      -> an insider of that firm traded ITS stock
  * reportingOwner == a watched fund -> the fund traded some other issuer's stock
"""
import json, re, time, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

UA = "sean@erised.me virgil/0.1"
_last = [0.0]

# open-market intent vs. compensation plumbing
SIGNAL_CODES = {"P": "buy", "S": "sell"}
NOISE_CODES = {"A": "grant", "M": "option exercise", "F": "tax withholding",
               "G": "gift", "C": "conversion", "D": "disposed to issuer",
               "X": "option exercise", "J": "other", "I": "discretionary"}


def _throttle(min_gap=0.11):          # SEC fair-use: stay under 10 req/s
    dt = time.time() - _last[0]
    if dt < min_gap:
        time.sleep(min_gap - dt)
    _last[0] = time.time()


def fetch(url, tries=3):
    for i in range(tries):
        _throttle()
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept-Encoding": "gzip, deflate"})
        try:
            r = urllib.request.urlopen(req, timeout=30)
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and i < tries - 1:
                time.sleep(2 ** i)
                continue
            raise
        except Exception:
            if i < tries - 1:
                time.sleep(1 + i)
                continue
            raise


# ---------------------------------------------------------------- bulk fetch

import threading
from concurrent.futures import ThreadPoolExecutor

_lock = threading.Lock()
_slot = [0.0]


def _reserve(min_gap):
    """Global rate limiter shared across worker threads."""
    with _lock:
        now = time.time()
        nxt = max(now, _slot[0]) + min_gap
        _slot[0] = nxt - min_gap
        wait = max(0.0, nxt - min_gap - now)
        _slot[0] = nxt
    if wait > 0:
        time.sleep(wait)


def fetch_many(urls, workers=8, min_gap=0.115, on_error=None):
    """Fetch many URLs under one global SEC rate limit (~9 req/s)."""
    out = {}

    def one(u):
        try:
            _reserve(min_gap)
            req = urllib.request.Request(u, headers={"User-Agent": UA,
                                                     "Accept-Encoding": "gzip, deflate"})
            r = urllib.request.urlopen(req, timeout=45)
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            return u, raw.decode("utf-8", "replace")
        except Exception as e:
            if on_error:
                on_error(u, e)
            return u, None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for u, body in ex.map(one, urls):
            out[u] = body
    return out


IDX_RE = re.compile(r"^(\S+)\s+(.+?)\s{2,}(\d+)\s+(\d{8})\s+(edgar/\S+)\s*$")


def daily_index(date_str):
    """All filings disseminated on one day. date_str = YYYYMMDD."""
    y, m = date_str[:4], int(date_str[4:6])
    q = (m - 1) // 3 + 1
    url = (f"https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/"
           f"form.{date_str}.idx")
    try:
        txt = fetch(url)
    except Exception:
        return []
    rows, started = [], False
    for line in txt.splitlines():
        if line.startswith("---"):
            started = True
            continue
        if not started or not line.strip():
            continue
        m = IDX_RE.match(line)
        if not m:
            continue
        form, company, cik, filed, fname = (m.group(1), m.group(2).strip(),
                                            m.group(3), m.group(4), m.group(5))
        rows.append({"form": form, "company": company, "cik": cik,
                     "filed": filed, "path": fname,
                     "url": "https://www.sec.gov/Archives/" + fname})
    return rows


OWNERSHIP_RE = re.compile(r"<ownershipDocument>.*?</ownershipDocument>", re.S)


def extract_ownership(submission_txt):
    """Pull the Form 4 XML out of a full .txt submission (one request)."""
    if not submission_txt:
        return None
    m = OWNERSHIP_RE.search(submission_txt)
    return m.group(0) if m else None


def submissions(cik):
    return json.loads(fetch(f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json"))


def recent_filings(cik, forms=None, limit=60):
    """Flatten the submissions 'recent' columnar block into dicts."""
    d = submissions(cik)
    r = d["filings"]["recent"]
    out = []
    for i in range(len(r["form"])):
        if forms and r["form"][i] not in forms:
            continue
        acc = r["accessionNumber"][i]
        doc = r["primaryDocument"][i]
        base = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{acc.replace('-', '')}/")
        # ownership forms expose an XSL-styled HTML view as primaryDocument;
        # the raw XML sits at the same path minus the xslF345XNN/ prefix
        raw = re.sub(r"^xsl[^/]*/", "", doc)
        out.append({
            "raw_url": base + raw,
            "headers_url": base + acc + "-index-headers.html",
            "cik": str(cik).zfill(10),
            "entity": d.get("name"),
            "form": r["form"][i],
            "filed": r["filingDate"][i],
            "period": r.get("reportDate", [""] * len(r["form"]))[i],
            "accession": acc,
            "doc": r["primaryDocument"][i],
            "url": (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                    f"{acc.replace('-', '')}/{r['primaryDocument'][i]}"),
            "index": (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                      f"{acc.replace('-', '')}/{acc}-index.htm"),
        })
        if len(out) >= limit:
            break
    return out


def _t(node, path, default=""):
    el = node.find(path)
    if el is None:
        return default
    # values are often wrapped in <value>
    v = el.find("value")
    if v is not None:
        return (v.text or "").strip()
    return (el.text or "").strip()


def _f(node, path):
    s = _t(node, path)
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_form4(xml_text, url=""):
    """Return a normalized Form 4 record, or None if unparseable."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8", "replace"))
    except ET.ParseError:
        m = re.search(r"<ownershipDocument>.*</ownershipDocument>", xml_text, re.S)
        if not m:
            return None
        try:
            root = ET.fromstring(m.group(0).encode("utf-8", "replace"))
        except ET.ParseError:
            return None

    owners = []
    for ro in root.findall("reportingOwner"):
        rel = ro.find("reportingOwnerRelationship")
        roles = []
        if rel is not None:
            if _t(rel, "isDirector") in ("1", "true"):      roles.append("Director")
            if _t(rel, "isOfficer") in ("1", "true"):        roles.append(_t(rel, "officerTitle") or "Officer")
            if _t(rel, "isTenPercentOwner") in ("1", "true"): roles.append("10% Owner")
            other = _t(rel, "otherText")
            if _t(rel, "isOther") in ("1", "true") and other: roles.append(other)
        owners.append({
            "cik": _t(ro, "reportingOwnerId/rptOwnerCik"),
            "name": _t(ro, "reportingOwnerId/rptOwnerName"),
            "roles": roles,
        })

    txns = []
    for tag, table in (("nonDerivativeTransaction", "I"), ("derivativeTransaction", "II")):
        for tx in root.iter(tag):
            code = _t(tx, "transactionCoding/transactionCode")
            ad = _t(tx, "transactionAmounts/transactionAcquiredDisposedCode")
            shares = _f(tx, "transactionAmounts/transactionShares")
            price = _f(tx, "transactionAmounts/transactionPricePerShare")
            txns.append({
                "table": table,
                "security": _t(tx, "securityTitle"),
                "date": _t(tx, "transactionDate"),
                "code": code,
                "action": SIGNAL_CODES.get(code) or NOISE_CODES.get(code) or code,
                "is_signal": code in SIGNAL_CODES,
                "acquired_disposed": ad,
                "shares": shares,
                "price": price,
                "value": (shares * price) if (shares and price) else None,
                "shares_after": _f(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction"),
                "direct": _t(tx, "ownershipNature/directOrIndirectOwnership") == "D",
            })

    return {
        "form": _t(root, "documentType") or "4",
        "period": _t(root, "periodOfReport"),
        "issuer_cik": _t(root, "issuer/issuerCik"),
        "issuer_name": _t(root, "issuer/issuerName"),
        "ticker": _t(root, "issuer/issuerTradingSymbol"),
        "rule_10b5_1": _t(root, "aff10b5One") in ("1", "true"),
        "owners": owners,
        "transactions": txns,
        "url": url,
    }


SUBJ_RE = re.compile(
    r"SUBJECT COMPANY:.*?COMPANY CONFORMED NAME:\s*(?P<name>.+?)\s*\n"
    r".*?CENTRAL INDEX KEY:\s*(?P<cik>\d+)", re.S)
TICKER_RE = re.compile(r"\n\s*(?:TRADING SYMBOL|SYMBOL):\s*(\S+)")


def subject_company(headers_url):
    """For SC 13D/G: pull the TARGET company out of the SGML header.

    The filer CIK identifies Icahn; the subject company identifies what he
    actually bought, which is the part that matters.
    """
    try:
        txt = fetch(headers_url)
    except Exception:
        return None
    m = SUBJ_RE.search(txt)
    if not m:
        return None
    return {"name": m.group("name").strip(), "cik": m.group("cik").zfill(10)}


def _local(el):
    return el.tag.rsplit("}", 1)[-1]


def parse_144(xml_text, url=""):
    """Form 144: notice of INTENT to sell restricted/control stock.

    Filed at or before the order goes in, so it leads the confirming Form 4
    by roughly two business days.
    """
    try:
        root = ET.fromstring(xml_text.encode("utf-8", "replace"))
    except ET.ParseError:
        return None

    def first(tag):
        for e in root.iter():
            if _local(e) == tag and (e.text or "").strip():
                return e.text.strip()
        return ""

    rels = [e.text.strip() for e in root.iter()
            if _local(e) == "relationshipToIssuer" and (e.text or "").strip()]

    tranches = []
    for blk in root.iter():
        if _local(blk) != "securitiesInformation":
            continue
        g = {}
        for e in blk.iter():
            t = _local(e)
            if t in ("securitiesClassTitle", "noOfUnitsSold", "aggregateMarketValue",
                     "approxSaleDate", "securitiesExchangeName"):
                g.setdefault(t, (e.text or "").strip())
        try:
            units = float(g.get("noOfUnitsSold") or 0)
        except ValueError:
            units = 0.0
        try:
            value = float(g.get("aggregateMarketValue") or 0)
        except ValueError:
            value = 0.0
        if units or value:
            tranches.append({"security": g.get("securitiesClassTitle", ""),
                             "units": units, "value": value,
                             "sale_date": g.get("approxSaleDate", ""),
                             "exchange": g.get("securitiesExchangeName", "")})

    return {
        "form": "144",
        "issuer_cik": first("issuerCik"),
        "issuer_name": first("issuerName"),
        "seller": first("nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold"),
        "relationships": rels,
        "tranches": tranches,
        "units": sum(t["units"] for t in tranches),
        "value": sum(t["value"] for t in tranches),
        "sale_date": tranches[0]["sale_date"] if tranches else "",
        "exchange": tranches[0]["exchange"] if tranches else "",
        "url": url,
    }


PCT_RE = re.compile(r"Percent of class[^0-9]{0,80}?([0-9]+(?:\.[0-9]+)?)\s*%", re.I)
AGG_RE = re.compile(r"Aggregate amount beneficially owned[^0-9]{0,120}?([0-9][0-9,\.]*)", re.I)


def parse_13d(doc_text, url=""):
    """Schedule 13D/G cover page.

    Filings from Dec 2024 on are structured XML; older ones are HTML and fall
    back to regex over the flattened cover page.
    """
    out = {"form": "13D", "issuer_name": "", "issuer_cik": "", "cusip": "",
           "amendment_no": "", "event_date": "", "persons": [], "purpose": "",
           "url": url}

    if "<edgarSubmission" in doc_text or "<reportingPersonInfo>" in doc_text:
        try:
            root = ET.fromstring(doc_text.encode("utf-8", "replace"))
        except ET.ParseError:
            root = None
        if root is not None:
            def first(tag):
                for e in root.iter():
                    if _local(e) == tag and (e.text or "").strip():
                        return e.text.strip()
                return ""
            out["issuer_name"] = first("issuerName")
            out["issuer_cik"] = first("issuerCIK")
            out["cusip"] = first("issuerCusipNumber")
            out["amendment_no"] = first("amendmentNo")
            out["event_date"] = first("dateOfEvent")
            for blk in root.iter():
                if _local(blk) != "reportingPersonInfo":
                    continue
                g = {}
                for e in blk.iter():
                    t = _local(e)
                    if t in ("reportingPersonName", "aggregateAmountOwned",
                             "percentOfClass", "typeOfReportingPerson",
                             "reportingPersonCIK"):
                        g.setdefault(t, (e.text or "").strip())
                def num(v):
                    try:
                        return float(str(v).replace(",", ""))
                    except (TypeError, ValueError):
                        return None
                out["persons"].append({
                    "name": g.get("reportingPersonName", ""),
                    "cik": g.get("reportingPersonCIK", ""),
                    "shares": num(g.get("aggregateAmountOwned")),
                    "percent": num(g.get("percentOfClass")),
                    "type": g.get("typeOfReportingPerson", ""),
                })
            # Item 4 (Purpose of Transaction) is nested HTML, not a text node
            for e in root.iter():
                if _local(e) == "item4":
                    body = re.sub(r"\s+", " ", "".join(e.itertext())).strip()
                    if body:
                        out["purpose"] = body[:900]
                        break
            if out["persons"]:
                return out

    # ---- legacy HTML cover page ----
    flat = re.sub(r"\s+", " ", re.sub(r"&nbsp;?", " ", re.sub(r"<[^>]+>", " ", doc_text)))
    pct = PCT_RE.search(flat)
    agg = AGG_RE.search(flat)
    if pct or agg:
        def num(v):
            try:
                return float(v.replace(",", ""))
            except (TypeError, ValueError, AttributeError):
                return None
        out["persons"].append({"name": "", "cik": "",
                               "shares": num(agg.group(1)) if agg else None,
                               "percent": num(pct.group(1)) if pct else None,
                               "type": ""})
    m = re.search(r"CUSIP\s*(?:No\.?|Number)?\s*:?\s*([0-9A-Z]{6,9})", flat, re.I)
    if m:
        out["cusip"] = m.group(1)
    return out


def parse_13f(xml_text):
    """Parse a 13F information table into holdings rows."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8", "replace"))
    except ET.ParseError:
        return []
    rows = []
    for it in root.iter():
        if not it.tag.endswith("infoTable"):
            continue
        g = lambda t: next((e.text or "").strip() for e in it.iter()
                           if e.tag.endswith(t)) if any(e.tag.endswith(t) for e in it.iter()) else ""
        def find(tag):
            for e in it.iter():
                if e.tag.endswith(tag):
                    return (e.text or "").strip()
            return ""
        try:
            value = float(find("value") or 0)
        except ValueError:
            value = 0.0
        try:
            shares = float(find("sshPrnamt") or 0)
        except ValueError:
            shares = 0.0
        rows.append({
            "issuer": find("nameOfIssuer"),
            "class": find("titleOfClass"),
            "cusip": find("cusip"),
            "value": value,          # USD (post-2023 filings are in dollars)
            "shares": shares,
            "sh_prn": find("sshPrnamtType"),
            "put_call": find("putCall"),
        })
    return rows


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
