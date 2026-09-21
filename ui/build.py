"""Render the static dashboard from the ingested data.

Data is inlined into the page so it works from a private GCS object with no
CORS or auth dance; the poller regenerates and re-uploads on every cycle.
"""
import hashlib
import html as _html
import re
import json, os, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def load(name, default):
    p = os.path.join(DATA, name)
    return json.load(open(p)) if os.path.exists(p) else default



CORP_TOKENS = {"LLC","L.L.C.","LP","L.P.","INC","INC.","CORP","CORP.","CORPORATION",
               "GROUP","HOLDINGS","PARTNERS","PARTNERSHIP","CAPITAL","MANAGEMENT",
               "ASSOCIATES","FUND","TRUST","COMPANY","CO","CO.","PLC","LTD","LTD.",
               "N.V.","S.A.","BANK","ADVISORS","ADVISERS","SECURITIES","INTERNATIONAL",
               "GP","HOLDCO","INVESTMENTS","ASSET","FINANCIAL","&"}


def is_corporate(name):
    up = name.upper().replace(",", " ").split()
    return any(t in CORP_TOKENS for t in up)


def person_name(name):
    """EDGAR files people as 'Last First Middle'; render them the human way.

    Corporate filers are left exactly as filed.
    """
    if not name or is_corporate(name):
        return name
    raw = name.replace(",", " ").split()
    if not (2 <= len(raw) <= 4):
        return name.title() if name.isupper() else name
    toks = [t.title() if t.isupper() or t.islower() else t for t in raw]
    toks = [t if len(t) > 2 or t.endswith(".") else t.upper() if len(t) == 1 else t
            for t in toks]
    last, rest = toks[0], toks[1:]
    return " ".join(rest + [last])


def normalize_actors(events, principals):
    """Only Form 3/4/5 owner names follow EDGAR's 'Last First Middle' convention.

    Form 144's seller field is free text - filers write "JAMES COSTOS" as often
    as "Schwab Charles R." - so reordering it would corrupt half the names.
    Those only get case-normalised.
    """
    for e in events:
        a = (e.get("actor") or "").strip()
        if not a or a in principals:
            continue
        if e.get("form") == "144":
            e["actor"] = a.title() if a.isupper() else a
        else:
            e["actor"] = person_name(a)
    return events


def aggregate(events):
    """Collapse one filing's same-day tranches into a single line.

    A director unloading in six fills is one decision, not six.
    """
    buckets, order = {}, []
    for e in events:
        k = (e.get("accession"), e.get("actor"), e.get("ticker"),
             e.get("action"), e.get("txn_date"), e.get("type"))
        if k not in buckets:
            b = dict(e)
            b["fills"] = 1
            buckets[k] = b
            order.append(k)
            continue
        b = buckets[k]
        b["fills"] += 1
        sh = (b.get("shares") or 0) + (e.get("shares") or 0)
        val = (b.get("value") or 0) + (e.get("value") or 0)
        b["shares"] = sh or None
        b["value"] = val or None
        b["price"] = (val / sh) if (sh and val) else b.get("price")
        if e.get("shares_after") is not None:
            b["shares_after"] = e["shares_after"]
    return [buckets[k] for k in order]


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Virgil — tracked investor filings</title>
<!-- The SVG carries its own colour-scheme query, so the mark follows the tab
     bar's theme; the PNGs are the fallback for browsers that ignore SVG icons. -->
<link rel="icon" type="image/svg+xml" href="assets/brand/favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="assets/brand/favicon-32.png">
<link rel="apple-touch-icon" sizes="180x180" href="assets/brand/apple-touch-icon.png">
<meta name="theme-color" content="#F7F7F4" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#111311" media="(prefers-color-scheme: dark)">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#FAFAF7; --paper-2:#F4F3EE; --card:#FFFFFF;
  --ink:#171612; --ink-2:#55534B; --ink-3:#8A877C;
  --rule:#E4E1D8; --rule-2:#EFEDE6;
  --accent:#2E4A40;
  --buy:#3F6B54; --buy-bg:#EDF3EF;
  --sell:#A05541; --sell-bg:#F8EFEB;
  --flag:#8A6D3B; --flag-bg:#F6F1E6;
  --radius:11px;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
}
/* Three states, not two: an explicit choice must beat the OS preference.
   Bare :root is light; the media query is guarded so [data-theme="light"]
   wins over a dark OS; and [data-theme="dark"] wins in the other direction. */
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --paper:#0D0E0D; --paper-2:#151714; --card:#141613;
    --ink:#EDEBE4; --ink-2:#A8A59B; --ink-3:#7A776E;
    --rule:#2C2C27; --rule-2:#232320;
    --accent:#9CC2B2;
    --buy:#7FB394; --buy-bg:#1B2620;
    --sell:#D08D77; --sell-bg:#2A1E1A;
    --flag:#C8A968; --flag-bg:#262115;
  }
}
:root[data-theme="dark"]{
  --paper:#0D0E0D; --paper-2:#151714; --card:#141613;
    --ink:#EDEBE4; --ink-2:#A8A59B; --ink-3:#7A776E;
    --rule:#2C2C27; --rule-2:#232320;
    --accent:#9CC2B2;
    --buy:#7FB394; --buy-bg:#1B2620;
    --sell:#D08D77; --sell-bg:#2A1E1A;
    --flag:#C8A968; --flag-bg:#262115;
}
*{box-sizing:border-box}
#stars{position:fixed;inset:0;width:100%;height:100%;z-index:0;pointer-events:none;
  opacity:0;transition:opacity .6s ease}
:root[data-theme="dark"] #stars{opacity:1}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) #stars{opacity:1}}
header,.wrap,footer{position:relative;z-index:1}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font:400 15px/1.55 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased;
  font-feature-settings:"tnum" 1,"cv05" 1;
}
.wrap{max-width:1180px;margin:0 auto;padding:0 32px}

/* ---------- masthead ---------- */
header{border-bottom:1px solid var(--rule);background:var(--paper);position:sticky;top:0;z-index:20;backdrop-filter:blur(8px)}
.bar{display:flex;align-items:baseline;gap:16px;padding:34px 0 22px}
.mark{font-family:"Instrument Serif",Georgia,serif;font-size:38px;letter-spacing:-.015em;
  line-height:1;color:var(--ink)}
.mark em{font-style:italic;color:var(--accent)}
.tag{font-size:12.5px;color:var(--ink-3);letter-spacing:.01em;align-self:flex-end;
  padding-bottom:4px}
.vintage{display:flex;flex-wrap:wrap;gap:4px 16px;padding:0 0 16px;font-size:11px;
  color:var(--ink-3);letter-spacing:.01em}
.vintage span{white-space:nowrap;opacity:.85}
.vintage b{color:var(--ink-2);font-weight:500}
.vintage span{white-space:nowrap}
.live{margin-left:auto;display:flex;align-items:center;gap:12px;font-size:12px;color:var(--ink-2)}
#stamp{cursor:help;border-bottom:1px dotted var(--rule)}
.themebtn{width:30px;height:30px;border-radius:50%;border:1px solid var(--rule);
  background:transparent;cursor:pointer;display:grid;place-items:center;padding:0;
  color:var(--ink-3);transition:.15s}
.themebtn:hover{border-color:var(--ink-3);color:var(--ink)}
.themebtn::before{content:"";width:13px;height:13px;border-radius:50%;
  background:currentColor;
  box-shadow:inset -4px -2px 0 0 var(--paper)}
:root[data-theme="light"] .themebtn::before{box-shadow:none;
  background:radial-gradient(circle,currentColor 45%,transparent 46%),
    repeating-conic-gradient(currentColor 0 8%,transparent 8% 25%)}
.dot{width:6px;height:6px;border-radius:50%;background:var(--buy);box-shadow:0 0 0 0 var(--buy);animation:p 2.4s infinite}
@keyframes p{0%{box-shadow:0 0 0 0 rgba(63,107,84,.45)}70%{box-shadow:0 0 0 7px rgba(63,107,84,0)}100%{box-shadow:0 0 0 0 rgba(63,107,84,0)}}

/* ---------- filters ---------- */
nav{display:flex;gap:7px;padding-bottom:20px;flex-wrap:wrap;align-items:center}
.chip{
  font:500 12.5px/1 Inter,sans-serif;color:var(--ink-2);
  padding:7px 13px;border:1px solid var(--rule);border-radius:999px;
  background:transparent;cursor:pointer;transition:.15s
}
.chip:hover{border-color:var(--ink-3);color:var(--ink)}
.chip[aria-pressed="true"]{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.chip .n{opacity:.5;margin-left:6px;font-variant-numeric:tabular-nums}
.tabs{display:flex;gap:2px;align-items:center;padding:4px 0 16px}
.tab{font:500 14.5px Inter,sans-serif;color:var(--ink-3);background:none;border:none;
  padding:7px 2px;margin-right:20px;cursor:pointer;border-bottom:2px solid transparent}
.tab:hover{color:var(--ink)}
.tab.on{color:var(--ink);border-bottom-color:var(--ink)}
.following{margin-left:auto;font-size:11.5px;color:var(--ink-3)}
.dgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:10px}
.dcard{border:1px solid var(--rule);border-radius:var(--radius);background:var(--card);
  padding:15px 16px;display:flex;gap:13px;align-items:center;min-height:78px;
  transition:border-color .12s}
.dcard:hover{border-color:var(--ink-3)}
.dcard .body{min-width:0;flex:1;overflow:hidden}
.dcard .nm{font-weight:600;font-size:14px;letter-spacing:-.01em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dcard .ti{font-size:12px;color:var(--ink-2);margin-top:2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dcard .mt{font-size:11.5px;color:var(--ink-3);margin-top:3px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dcard a,.dcard .mt a{color:var(--ink-2);text-decoration:none;border-bottom:1px solid var(--rule)}
.dcard a:hover{color:var(--ink);border-bottom-color:var(--ink-3)}
.stock a{color:var(--ink-2)}
.fbtn{font:500 11.5px Inter,sans-serif;padding:5px 11px;border-radius:999px;cursor:pointer;
  border:1px solid var(--rule);background:transparent;color:var(--ink-2);white-space:nowrap}
.fbtn:hover{border-color:var(--ink-3);color:var(--ink)}
.fbtn.on{background:var(--buy);border-color:var(--buy);color:#fff}
.dsec{font:600 11px Inter,sans-serif;letter-spacing:.08em;text-transform:uppercase;
  color:var(--ink-3);margin:26px 0 12px;display:flex;align-items:center;gap:12px}
.dsec::after{content:"";flex:1;height:1px;background:var(--rule)}
.sub2{text-transform:none;letter-spacing:0;font-weight:400}
.filterlegend{margin:16px 0 0;padding:12px 15px;border-radius:8px;background:var(--paper-2);border-left:2px solid var(--accent);font-size:13px;line-height:1.55;color:var(--ink-2)}
.subtabs{display:flex;gap:2px;margin:18px 0 4px;border-bottom:1px solid var(--rule)}
.stab{font:500 13px Inter,sans-serif;color:var(--ink-3);background:none;border:none;
  padding:9px 2px;margin-right:22px;cursor:pointer;border-bottom:2px solid transparent}
.stab:hover{color:var(--ink)}
.stab.on{color:var(--ink);border-bottom-color:var(--ink)}
.stab .n{opacity:.5;margin-left:6px;font-variant-numeric:tabular-nums}
.spacer{flex:1}
#q{font:400 13px Inter,sans-serif;color:var(--ink);background:var(--card);
   border:1px solid var(--rule);border-radius:999px;padding:7px 14px;width:230px;
   outline:none;transition:.15s}
#q:focus{border-color:var(--ink-3);width:280px}
#q::placeholder{color:var(--ink-3)}

.stock{border:1px solid var(--rule);border-radius:14px;background:var(--card);
  padding:28px 30px;margin:28px 0 8px}
.stock h3{margin:0;font:600 19px Inter,sans-serif;letter-spacing:-.01em}
.stock .sub{font-size:12.5px;color:var(--ink-3);margin-top:3px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));
  gap:14px;margin:18px 0 6px;padding:15px 0;border-top:1px solid var(--rule-2);
  border-bottom:1px solid var(--rule-2)}
.kpi b{display:block;font:600 19px Inter,sans-serif;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.kpi span{font-size:11px;color:var(--ink-3);text-transform:uppercase;letter-spacing:.06em}
.who-tbl{width:100%;border-collapse:collapse;margin-top:14px;font-size:13px}
.who-tbl th{text-align:left;font:600 10.5px Inter,sans-serif;letter-spacing:.07em;
  text-transform:uppercase;color:var(--ink-3);padding:0 8px 8px 0;border-bottom:1px solid var(--rule)}
.who-tbl td{padding:9px 8px 9px 0;border-bottom:1px solid var(--rule-2);vertical-align:top}
.who-tbl .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.who-tbl tr:last-child td{border-bottom:none}
.role{font-size:11.5px;color:var(--ink-3)}
.chartwrap{margin:14px 0 4px}
.sincelab{font-size:11.5px;color:var(--ink-3);font-weight:400;margin-left:2px}
.spin{display:inline-block;width:11px;height:11px;margin-right:6px;
  border:2px solid var(--rule);border-top-color:var(--ink-2);border-radius:50%;
  vertical-align:-1px;animation:spin .9s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.spin{animation:none}}
.chooser{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 14px}
.chooserlab{font:500 10.5px/1 var(--mono,ui-monospace);letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-3)}
.pickbtn{display:flex;flex-direction:column;align-items:flex-start;gap:1px;
  border:1px solid var(--rule);background:var(--card);color:var(--ink);
  border-radius:7px;padding:6px 11px;cursor:pointer;font:500 13px Inter,sans-serif;
  text-align:left;transition:.15s}
.pickbtn small{font-weight:400;font-size:11px;color:var(--ink-3)}
.pickbtn:hover{border-color:var(--ink-3)}
.pickbtn.on{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.pickbtn.on small{color:var(--paper-2,#ccc)}
.chartmsg{height:150px;display:grid;place-items:center;text-align:center;
  font-size:12.5px;color:var(--ink-3);padding:0 20px}
.chart svg{display:block;overflow:visible}
.chart .baseline{stroke:var(--rule);stroke-width:1;stroke-dasharray:3 3}
.chart .cross{stroke:var(--ink-3);stroke-width:1;stroke-dasharray:3 3;pointer-events:none}
.chart svg{cursor:crosshair}
.ranges{display:flex;gap:4px;margin-bottom:8px}
.rbtn{font:500 11px Inter,sans-serif;color:var(--ink-3);background:transparent;
  border:1px solid var(--rule);border-radius:6px;padding:4px 9px;cursor:pointer}
.rbtn:hover{color:var(--ink)}
.rbtn.on{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.readout{display:flex;align-items:baseline;gap:10px;margin-top:6px;font-size:12px;
  color:var(--ink-3);font-variant-numeric:tabular-nums}
.readout b{font-size:15px;color:var(--ink);font-weight:600}
.pxbar{display:flex;align-items:center;gap:22px;flex-wrap:wrap;margin:18px 0 2px;
  padding:15px 17px;border:1px solid var(--rule);border-radius:var(--radius);background:var(--paper-2)}
.pxnow{font:600 26px Inter,sans-serif;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.pxlab{font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--ink-3)}
.pxdelta{font:600 15px Inter,sans-serif;font-variant-numeric:tabular-nums}
.spark{margin-left:auto}
#tip{position:fixed;z-index:90;max-width:330px;padding:12px 14px;border-radius:9px;
  background:var(--card);border:1px solid var(--rule);color:var(--ink);
  box-shadow:0 8px 26px rgba(0,0,0,.13);font-size:12.5px;line-height:1.55;
  pointer-events:none;opacity:0;transition:opacity .1s}
#tip.on{opacity:1}
#tip .t{font-weight:600;margin-bottom:6px}
#tip dl{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;margin:0}
#tip dt{color:var(--ink-3)}
#tip dd{margin:0;font-variant-numeric:tabular-nums;text-align:right}
#tip .warn{margin-top:9px;padding-top:8px;border-top:1px solid var(--rule-2);
  color:var(--ink-3);font-size:11.5px;line-height:1.45}
[data-tip]{cursor:help}
.perf{font-variant-numeric:tabular-nums;white-space:nowrap}
.perf small{display:block;color:var(--ink-3);font-size:10.5px}
.recency{margin:16px 0 4px;padding:11px 14px;border-radius:8px;font-size:12.5px;
  background:var(--paper-2);border-left:2px solid var(--ink-3);color:var(--ink-2)}
.recency.stale{border-left-color:var(--flag);background:var(--flag-bg)}
.recency b{color:var(--ink)}
.sechead{font:600 11px Inter,sans-serif;letter-spacing:.07em;text-transform:uppercase;
  color:var(--ink-3);margin:26px 0 0}
.lagnote{font-size:11.5px;color:var(--ink-3);margin:10px 0 0;line-height:1.5}
.nores{padding:22px;border:1px dashed var(--rule);border-radius:var(--radius);
  color:var(--ink-3);font-size:13px;background:var(--paper-2)}
.nores code{font:500 12px var(--mono);color:var(--ink-2);background:var(--card);
  padding:2px 6px;border-radius:4px;border:1px solid var(--rule)}

/* ---------- notice ---------- */
.notice{margin:34px 0 6px;font-size:12.5px;color:var(--ink-3)}
.notice summary{cursor:pointer;list-style:none;display:inline-flex;align-items:center;gap:7px;
  color:var(--ink-3);transition:color .12s}
.notice summary::-webkit-details-marker{display:none}
.notice summary::before{content:"";width:5px;height:5px;border-right:1px solid currentColor;
  border-bottom:1px solid currentColor;transform:rotate(-45deg);transition:transform .15s}
.notice[open] summary::before{transform:rotate(45deg)}
.notice summary:hover{color:var(--ink-2)}
.noticebody{max-width:74ch;margin:12px 0 0;padding:14px 16px;border-radius:var(--radius);
  background:var(--paper-2);border:1px solid var(--rule);line-height:1.6;color:var(--ink-2)}
.noticebody b{color:var(--ink);font-weight:600}

/* ---------- section heads ---------- */
h2{
  font:500 11.5px/1 Inter,sans-serif;letter-spacing:.11em;text-transform:uppercase;
  color:var(--ink-3);margin:46px 0 16px;display:flex;align-items:center;gap:14px
}
h2::after{content:"";flex:1;height:1px;background:var(--rule)}

/* ---------- feed ---------- */
.feed{border:1px solid var(--rule);border-radius:14px;overflow:hidden;background:var(--card)}
.row{
  display:grid;grid-template-columns:74px 1fr 132px 150px 92px 46px;
  gap:16px;align-items:center;padding:13px 18px;
  border-bottom:1px solid var(--rule-2);text-decoration:none;color:inherit;
  transition:background .12s
}
.row:last-child{border-bottom:none}
.row:hover{background:var(--paper-2)}
.rowwrap{border-bottom:1px solid var(--rule-2)}
.rowwrap:last-child{border-bottom:none}
.rowwrap .row{border-bottom:none}
.rowwrap.open{background:var(--paper-2)}
.tklink{font:600 13.5px Inter,sans-serif;letter-spacing:-.01em;color:var(--ink);
  background:none;border:none;padding:0;cursor:pointer;
  border-bottom:1px solid transparent;transition:border-color .12s}
.tklink:hover,.tklink:focus-visible{border-bottom-color:var(--ink)}
.rowwrap.open .tklink,tr.holdrow.open .tklink{border-bottom-color:var(--accent);color:var(--accent)}
tr.holdrow.open{background:var(--paper-2)}
tr.holdexp>td{padding:0;border-bottom:1px solid var(--rule)}
.filing{font-size:11px;color:var(--ink-3);text-decoration:none;justify-self:end;
  border-bottom:1px solid transparent}
.filing:hover{color:var(--ink-2);border-bottom-color:var(--rule)}
.expand{padding:2px 18px 20px;background:var(--paper-2)}
.exp-head{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;margin-bottom:2px}
.exp-name{font-weight:600;font-size:15px}
.exp-sub{font-size:12px;color:var(--ink-3)}
.exp-actions{margin-left:auto;display:flex;gap:8px}
.exp-grid{display:grid;grid-template-columns:1.25fr 1fr;gap:26px;align-items:start}
@media (max-width:900px){.exp-grid{grid-template-columns:1fr}}
.empty-state{padding:58px 24px;text-align:center;border:1px dashed var(--rule);
  border-radius:var(--radius);background:var(--card);margin-top:22px}
.empty-state h3{margin:0 0 8px;font:600 17px Inter,sans-serif;letter-spacing:-.01em}
.empty-state p{margin:0 auto;max-width:60ch;color:var(--ink-2);font-size:13.5px;line-height:1.6}
.empty-state a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--rule)}
.empty-state a:hover{border-bottom-color:var(--ink-3)}
a{color:var(--ink-2)}
.starter{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:18px}
.when{font-size:11.5px;color:var(--ink-3);font-variant-numeric:tabular-nums;white-space:nowrap}
.who{min-width:0}
.who .nm{font-weight:500;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.who .rl{font-size:11.5px;color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.act{display:flex;align-items:center;gap:7px}
.pill{
  font:600 10.5px/1 Inter,sans-serif;letter-spacing:.05em;text-transform:uppercase;
  padding:5px 8px;border-radius:5px;white-space:nowrap
}
.buy{background:var(--buy-bg);color:var(--buy)}
.sell{background:var(--sell-bg);color:var(--sell)}
.act13d{background:var(--flag-bg);color:var(--flag)}
.presale{background:var(--sell-bg);color:var(--sell);border:1px dashed var(--sell)}
.cl{border:1px solid var(--rule);border-radius:var(--radius);background:var(--card);
    padding:15px 17px;display:grid;grid-template-columns:70px 1fr auto;gap:15px;align-items:center;
    text-decoration:none;color:inherit;transition:.15s}
.cl:hover{border-color:var(--buy);transform:translateY(-1px)}
.cl .tk{font-weight:600;font-size:16px;letter-spacing:-.01em}
.cl .co{font-size:12px;color:var(--ink-3);margin-top:2px}
.cl .ppl{font-size:12px;color:var(--ink-2);line-height:1.5}
.cl .tot{text-align:right;font-variant-numeric:tabular-nums}
.cl .tot b{font-size:15px;font-weight:600;display:block}
.cl .tot span{font-size:11px;color:var(--ink-3)}
.count{display:inline-grid;place-items:center;width:34px;height:34px;border-radius:50%;
  background:var(--buy-bg);color:var(--buy);font-weight:600;font-size:14px}
.clgrid{display:grid;gap:10px}
.comp{background:var(--paper-2);color:var(--ink-3);border:1px solid var(--rule)}
.sec{min-width:0}
.sec .tk{font-weight:600;font-size:13.5px;letter-spacing:-.01em}
.sec .is{font-size:11.5px;color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.amt{text-align:right;font-variant-numeric:tabular-nums;font-size:13.5px;font-weight:500}
.amt .sh{display:block;font-size:11px;color:var(--ink-3);font-weight:400}
.muted{color:var(--ink-3);font-weight:400}
.tenb{font-size:10px;color:var(--ink-3);border:1px solid var(--rule);padding:2px 5px;border-radius:4px;white-space:nowrap}

/* ---------- people ---------- */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(228px,1fr));gap:12px}
.p{
  border:1px solid var(--rule);border-radius:var(--radius);padding:16px;
  background:var(--card);display:flex;gap:13px;align-items:flex-start;
  text-decoration:none;color:inherit;transition:.15s
}
.p:hover{border-color:var(--ink-3);transform:translateY(-1px)}
.flogo{width:15px;height:15px;border-radius:3px;vertical-align:-3px;flex:none;
  object-fit:contain;filter:none}
.livedot{display:inline-block;width:5px;height:5px;border-radius:50%;background:var(--buy);
  margin-right:5px;vertical-align:1px}
.av{width:46px;height:46px;border-radius:50%;object-fit:cover;flex:none;
  background:var(--paper-2);filter:grayscale(1) contrast(1.04);
  box-shadow:0 0 0 1px var(--rule)}
/* declared after .av on purpose: company marks keep their colour */
img.av.flogo-lg{width:46px;height:46px;object-fit:cover;background:transparent;
  padding:0;border-radius:10px;filter:none;image-rendering:auto;
  box-shadow:0 0 0 1px var(--rule)}
img.av.flogo-lg.wide{width:66px;height:40px;border-radius:7px;padding:5px 7px}
.flogo{background:#fff;padding:1px;border-radius:3px}
.mono{
  width:46px;height:46px;border-radius:50%;flex:none;display:grid;place-items:center;
  font:500 15px/1 Inter,sans-serif;letter-spacing:.04em;color:var(--ink);
  background:var(--paper-2);box-shadow:inset 0 0 0 1px var(--rule)
}
.p .body{min-width:0;flex:1}
.p .nm{font-weight:600;font-size:14px;letter-spacing:-.01em}
.p .ti{font-size:12px;color:var(--ink-2);margin-top:1px}
.p .fm{font-size:11.5px;color:var(--ink-3);margin-top:5px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.badge{font:500 10px/1 Inter,sans-serif;padding:3px 6px;border-radius:4px;background:var(--paper-2);color:var(--ink-2);border:1px solid var(--rule)}

/* ---------- empty / footer ---------- */
.empty{padding:44px 20px;text-align:center;color:var(--ink-3);font-size:13.5px}
footer{border-top:1px solid var(--rule);margin-top:52px;padding:26px 0 46px;font-size:12px;color:var(--ink-3);line-height:1.65}
footer a{color:var(--ink-2)}
footer .cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:24px}
footer h3{font:600 11px/1 Inter,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-2);margin:0 0 8px}
kbd{font:500 11px var(--mono);border:1px solid var(--rule);border-bottom-width:2px;border-radius:4px;padding:1px 5px;color:var(--ink-2)}

@media (max-width:820px){
  .row{grid-template-columns:1fr auto;gap:6px 12px;padding:14px 16px}
  .filing{display:none}
  .when{grid-column:1/-1;order:-1}
  .act{grid-column:2;justify-self:end}
  .sec{grid-column:1}
  .amt{grid-column:2;justify-self:end}
  .who{grid-column:1/-1}
  .wrap{padding:0 18px}
}
</style>
</head>
<body>
<canvas id="stars" aria-hidden="true"></canvas>
<header>
  <div class="wrap">
    <div class="bar">
      <div class="mark">Vir<em>gil</em></div>
      <div class="tag">tracked investor filings &middot; SEC EDGAR</div>
      <div class="live">
        <button class="themebtn" id="theme" title="Switch theme" aria-label="Switch theme"></button>
        <span class="dot"></span><span id="stamp" tabindex="0">—</span>
      </div>
    </div>
    <div class="vintage" id="vintage" hidden></div>
    <div class="tabs">
      <button class="tab on" data-tab="feed">Feed</button>
      <button class="tab" data-tab="discover">Discover</button>
      <span class="following" id="followcount"></span>
    </div>
    <nav id="filters">
      <button class="chip" data-f="all" aria-pressed="true" data-tip="Every disclosure below, newest first.">All</button>
      <button class="chip" data-f="buy" data-tip="Open-market purchases (Form 4 code P). An insider spending their own money — the strongest single signal here.">Buys<span class="n" id="c-buy"></span></button>
      <button class="chip" data-f="sell" data-tip="Open-market sales (Form 4 code S). Common and often routine diversification, so weigh against the person's own pattern.">Sells<span class="n" id="c-sell"></span></button>
      <button class="chip" data-f="activist" data-tip="Schedule 13D filings. Crossing 5% while declaring intent to influence the company — board seats, a sale, a strategy change. Filed within 2 business days of a material change.">Activist 13D<span class="n" id="c-activist"></span></button>
      <button class="chip" data-f="insider" data-tip="A Section 16 officer or director trading their own employer's stock.">Insider<span class="n" id="c-insider"></span></button>
      <button class="chip" data-f="fund_trade" data-tip="A tracked fund acting as a >10% owner of some other company's stock.">Fund<span class="n" id="c-fund"></span></button>
      <button class="chip" data-f="presale" data-tip="Form 144 — notice of intent to sell restricted stock, filed at or before the trade. The only signal here that leads rather than lags.">Pre-sale 144<span class="n" id="c-presale"></span></button>
      <input id="q" type="search" placeholder="Ticker, company, fund or investor…"
             autocomplete="off" spellcheck="false">
      <span class="spacer"></span>
      <button class="chip" id="toggle-comp" aria-pressed="false" data-tip="Grants, option exercises and tax withholding. Compensation plumbing, not a decision. Hidden by default.">Show comp &amp; grants<span class="n" id="c-comp"></span></button>
    </nav>
  </div>
</header>

<div class="wrap">
  <p class="filterlegend" id="filterlegend" hidden></p>
  <details class="notice">
    <summary>What this can and cannot see</summary>
    <div class="noticebody">
      Everything here is a mandatory public disclosure, so the lag is structural.
      Form&nbsp;4 insider trades arrive within <b>2 business days</b> and Schedule&nbsp;13D
      activist stakes within <b>5</b>. Quarterly 13F portfolios lag <b>45–135 days</b> and are
      long-only — a fund's ordinary sub-5% positions are invisible until the next one.
      Nothing here is real-time, and nothing here is advice.
    </div>
  </details>

  <div id="tip" role="tooltip" aria-hidden="true"></div>
  <div id="stockpanel"></div>

  <!-- FEED: only what the reader follows. Everything else lives in Discover. -->
  <section id="feedview">
    <div id="feedempty" hidden></div>
    <div id="feedhead" hidden>
      <h2 id="h-feed">Your feed <span class="sub2" id="feedcount"></span></h2>
    </div>
    <p class="filterlegend" id="filterlegend" hidden></p>
    <div class="feed" id="feed"></div>
  </section>

  <!-- DISCOVER: the whole market, for browsing and choosing what to follow. -->
  <section id="discover" hidden>
    <div id="discoverbody"></div>

    <h2 id="h-stakes">Activist stakes <span class="sub2">— the latest declared stake per filer and target. Filing a 13D means stating an intent to influence the company; a stake can sit below 5% once trimmed</span></h2>
    <div class="clgrid" id="stakes"></div>

    <h2 id="h-clusters">Cluster buys <span class="sub2">— three or more separate insiders buying the same company on the open market, across the whole market</span></h2>
    <div class="clgrid" id="clusters"></div>

    <h2 id="h-all">Everything <span class="sub2">— every filing from every tracked entity, newest first</span></h2>
    <div class="feed" id="allfeed"></div>

    <h2 id="h-principals">Investors <span class="sub2" id="pcount"></span></h2>
    <div class="grid" id="people"></div>

    <h2 id="h-firms">Firms <span class="sub2">— Section 16 insiders of these public companies</span></h2>
    <div class="grid" id="firms"></div>
  </section>
</div>

<footer>
  <div class="wrap cols">
    <div>
      <h3>Source</h3>
      SEC EDGAR — Forms 4, 3, SC 13D/G, 13F-HR.
      Fetched directly from <a href="https://www.sec.gov/edgar">sec.gov</a>, no vendor in between.
    </div>
    <div>
      <h3>Refresh</h3>
      Poller runs every <span id="interval">—</span>. Page reloads itself on the same cadence.
      Press <kbd>R</kbd> to reload now.
    </div>
    <div>
      <h3>Portraits</h3>
      Wikipedia where a free-licensed image exists and the identity could be verified,
      otherwise the firm's own leadership page. Anyone unmatched renders as a monogram
      rather than risk showing the wrong face.
    </div>
    <div>
      <h3>Not advice</h3>
      Personal research tool. Disclosure data is lagged, incomplete, and frequently
      reflects pre-scheduled 10b5-1 plans rather than conviction.
    </div>
  </div>
</footer>

<script>
let EVENTS = /*__EVENTS__*/[];
const PEOPLE = /*__PEOPLE__*/{};
const FIRMS  = /*__FIRMS__*/{};
let CLUSTERS = /*__CLUSTERS__*/[];
const STOCKS = /*__STOCKS__*/{};
const HOLDINGS = /*__HOLDINGS__*/{};
const MEMBERS  = /*__MEMBERS__*/{};
let QUOTES   = /*__QUOTES__*/{};
const QMETA    = /*__QMETA__*/{};
const LOGOS    = /*__LOGOS__*/{};
const CONGTKR  = /*__CONGTKR__*/{};
const TICKER_LIST = /*__TICKERS__*/[];   // names only; detail is fetched
const TICKERS  = {};                     // filled in as tickers are opened
const META   = /*__META__*/{};

const fmtUSD = v => v == null ? null :
  v >= 1e9 ? "$" + (v/1e9).toFixed(2) + "B" :
  v >= 1e6 ? "$" + (v/1e6).toFixed(2) + "M" :
  v >= 1e3 ? "$" + (v/1e3).toFixed(0) + "K" : "$" + v.toFixed(0);
const isoDay = d => (d && d.length === 8) ? `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}` : (d||"");
const fmtSh = n => n == null ? "" : n.toLocaleString("en-US", {maximumFractionDigits:0}) + " sh";

function ago(d){
  if(!d) return "";
  const t = new Date(d + "T00:00:00Z"), now = new Date();
  const days = Math.floor((now - t) / 864e5);
  if(days <= 0) return "today";
  if(days === 1) return "yesterday";
  if(days < 30) return days + "d ago";
  if(days < 365) return Math.floor(days/30) + "mo ago";
  return Math.floor(days/365) + "y ago";
}

const esc = s => (s||"").replace(/[&<>"']/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

// Initials on the page ground rather than a coloured disc: reads as a
// deliberate placeholder instead of a broken image.
function monogram(name){
  const parts = (name||"?").replace(/[^A-Za-z ]/g,"").trim().split(/\s+/);
  const ini = ((parts[0]||"?")[0] + (parts.length>1 ? parts[parts.length-1][0] : "")).toUpperCase();
  return `<div class="mono" title="${esc(name||"")}">${ini}</div>`;
}

function tickerMark(t){
  // wordmark on the page ground, matching the person monogram
  return `<div class="mono" style="font-size:12px;letter-spacing:.04em">${esc(t)}</div>`;
}

const LEGEND = {"all": "Every disclosure below, newest first.", "buy": "Open-market purchases (Form 4 code P) \u2014 an insider spending their own money. The strongest single signal here, and it lands about 2 days after the trade.", "sell": "Open-market sales (Form 4 code S). Common and often routine diversification, so weigh it against that person's own pattern rather than reading every sale as a warning.", "activist": "Schedule 13D filings \u2014 crossing 5% of a company while declaring intent to influence it: board seats, a sale, a strategy change. Amendments are due within 2 business days.", "insider": "A Section 16 officer or director trading their own employer's stock. Exact date and execution price, disclosed within 2 business days.", "fund_trade": "A tracked fund acting as a >10% owner of some other company's stock \u2014 which is what forces them onto Form 4 rather than waiting for a quarterly 13F.", "presale": "Form 144 \u2014 notice of intent to sell restricted stock, filed at or before the trade. The only signal here that leads rather than lags.", "comp": "Grants, option exercises and tax withholding. Compensation plumbing rather than a decision, so it is hidden unless you ask for it."};

let filter = "all", showComp = false, query = "";

function aggregateBy(trades, action){
  const m = new Map();
  trades.filter(t => t.action === action).forEach(t => {
    const k = t.who_cik || t.who;
    const a = m.get(k) || {who: t.who, roles: new Set(), shares: 0, value: 0,
                           n: 0, last: "", tracked: t.tracked};
    a.shares += t.shares || 0; a.value += t.value || 0; a.n += 1;
    if(t.perf && !a.perf) a.perf = t.perf;
    (t.roles || []).forEach(r => a.roles.add(r));
    if(t.filed > a.last) a.last = t.filed;
    m.set(k, a);
  });
  return [...m.values()].sort((x, y) => y.value - x.value);
}

function sparkline(series, w, h){
  if(!series || series.length < 2) return "";
  const vals = series.map(p => p[1]);
  const lo = Math.min(...vals), hi = Math.max(...vals), rng = (hi - lo) || 1;
  const pts = series.map((p, i) => [
    (i / (series.length - 1)) * w,
    h - ((p[1] - lo) / rng) * (h - 4) - 2,
  ]);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  const rising = vals[vals.length - 1] >= vals[0];
  const col = rising ? "var(--buy)" : "var(--sell)";
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"
    role="img" aria-label="${series.length}-day price history">
    <path d="${d}" fill="none" stroke="${col}" stroke-width="1.6"
      stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${pts[pts.length-1][0].toFixed(1)}" cy="${pts[pts.length-1][1].toFixed(1)}"
      r="2.6" fill="${col}"/></svg>`;
}

// -1 is not a slice: the day view is five-minute intraday, a different series
// fetched on demand. Everything else is a window onto the daily closes.
const RANGES = [["1D",-1],["1W",5],["1M",22],["3M",64],["6M",128],["1Y",252],["All",0]];

const QRANGES = [["1Y",4],["2Y",8],["All",0]];

function priceChart(series, id, mode, ticker){
  if(!series || series.length < 2) return "";
  const q = mode === "quarterly";
  // Offer only the ranges this series can tell apart. A 1Y button on ten bars
  // renders the same picture as 1M and 3M, which is what made every control on
  // the chart look broken — they were all slicing past the end of the data.
  const all = q ? QRANGES : RANGES;
  const ranges = all.filter(([, n], i) =>
    n === -1 ? !!ticker
             : n === 0 || n < series.length ||
               (i > 0 && all[i - 1][1] > 0 && all[i - 1][1] < series.length));
  const active = Math.max(0, ranges.findIndex(([, n]) => n === 0 || n >= 60));
  return `<div class="chartwrap" id="cw-${id}" data-mode="${q ? "quarterly" : "daily"}"
      ${ticker ? `data-ticker="${esc(ticker)}"` : ""}
      data-series='${JSON.stringify(series)}'>
    <div class="ranges">${ranges.map((r,i) =>
      `<button class="rbtn${i===active?" on":""}" data-n="${r[1]}">${r[0]}</button>`).join("")}</div>
    <div class="chart"></div>
    <div class="readout"><span class="rdate"></span><b class="rprice"></b></div>
  </div>`;
}

// The shards carry a short series so a search stays small; the full history
// lives beside the data and is pulled in once, the first time a chart is shown.
async function loadFullSeries(wrap){
  const tk = wrap.dataset.ticker;
  if(!tk || wrap.dataset.full === "1" || wrap.dataset.full === "x") return;
  wrap.dataset.full = "x";                       // don't retry on every redraw
  const base = DATA_BASE || "";
  try{
    const r = await fetch(`${base}data/prices/${encodeURIComponent(tk)}.json`);
    if(!r.ok) return;
    const d = await r.json();
    if(!Array.isArray(d.series) || d.series.length < 2) return;
    const cur = JSON.parse(wrap.dataset.series);
    if(d.series.length <= cur.length) return;
    wrap.dataset.series = JSON.stringify(d.series);
    wrap.dataset.full = "1";
    const host = wrap.querySelector(".ranges");
    if(host){
      const on = host.querySelector(".rbtn.on")?.dataset.n;
      host.innerHTML = RANGES
        .filter(([, n], i) => n === -1 ? true
                : n === 0 || n < d.series.length ||
                  (i > 0 && RANGES[i - 1][1] > 0 && RANGES[i - 1][1] < d.series.length))
        .map(([lab, n]) => `<button class="rbtn${String(n) === on ? " on" : ""}"
             data-n="${n}">${lab}</button>`).join("");
      if(!host.querySelector(".rbtn.on")) host.lastElementChild?.classList.add("on");
    }
    drawChart(wrap);
  }catch(e){ /* the short series still draws */ }
}

async function loadIntraday(wrap){
  const tk = wrap.dataset.ticker;
  const host = wrap.querySelector(".chart");
  if(!tk){ return; }
  host.innerHTML = `<div class="chartmsg">Loading today…</div>`;
  try{
    const r = await fetch(`/api/intraday?t=${encodeURIComponent(tk)}`);
    if(!r.ok) throw new Error(String(r.status));
    const d = await r.json();
    if(!Array.isArray(d.series) || d.series.length < 2) throw new Error("empty");
    wrap._intraday = d;
    drawChart(wrap);
  }catch(e){
    // Outside market hours there is no session to draw. Say so and fall back,
    // rather than leaving the reader looking at an empty frame.
    host.innerHTML = `<div class="chartmsg">No trading session today —
      showing the most recent week instead.</div>`;
    const wk = wrap.querySelector('.rbtn[data-n="5"]') ||
               wrap.querySelector('.rbtn[data-n="22"]');
    if(wk){
      wrap.querySelectorAll(".rbtn").forEach(x => x.classList.remove("on"));
      wk.classList.add("on");
      setTimeout(() => drawChart(wrap), 900);
    }
  }
}

function drawChart(wrap){
  const n = +(wrap.querySelector(".rbtn.on")?.dataset.n || 0);
  const intraday = n === -1;
  if(intraday && !wrap._intraday){ loadIntraday(wrap); return; }
  const all = intraday ? wrap._intraday.series : JSON.parse(wrap.dataset.series);
  const data = (intraday || !n) ? all : all.slice(-n);
  const host = wrap.querySelector(".chart");
  const W = host.clientWidth || 640, H = 150, PAD = 4;
  const vals = data.map(d => d[1]);
  const lo = Math.min(...vals), hi = Math.max(...vals), rng = (hi - lo) || 1;
  const x = i => (i / (data.length - 1)) * W;
  const y = v => H - PAD - ((v - lo) / rng) * (H - PAD * 2);
  const line = data.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d[1]).toFixed(1)}`).join("");
  const area = `${line}L${W},${H}L0,${H}Z`;
  // An opening gap belongs in the day's number, so intraday measures from the
  // prior close rather than from the first print of the session.
  const first = (intraday && wrap._intraday.prev_close) || data[0][1];
  const up = data[data.length - 1][1] >= first;
  const col = up ? "var(--buy)" : "var(--sell)";

  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}"
      preserveAspectRatio="none" role="img" aria-label="price history">
    <defs><linearGradient id="g-${wrap.id}" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stop-color="${col}" stop-opacity=".18"/>
      <stop offset="100%" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
    <line class="baseline" x1="0" x2="${W}" y1="${y(first)}" y2="${y(first)}"/>
    <path d="${area}" fill="url(#g-${wrap.id})"/>
    <path d="${line}" fill="none" stroke="${col}" stroke-width="1.7"
      stroke-linejoin="round" stroke-linecap="round"/>
    <line class="cross" x1="0" x2="0" y1="0" y2="${H}" style="opacity:0"/>
    <circle class="dot" r="3.4" fill="${col}" style="opacity:0"/>
  </svg>`;

  const svg = host.querySelector("svg");
  const cross = svg.querySelector(".cross"), dot = svg.querySelector(".dot");
  const rd = wrap.querySelector(".rdate"), rp = wrap.querySelector(".rprice");
  const show = i => {
    const d = data[i];
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i));
    cross.style.opacity = 1;
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(d[1])); dot.style.opacity = 1;
    // A fund whose first reported quarter is zero — Norges Bank's history
    // starts before it filed — has no percentage to show from it. Dividing by
    // zero gave Infinity, and the guarded null form threw on .toFixed, which
    // left the whole panel blank with no clue why.
    const ch = (first && isFinite(d[1])) ? ((d[1] - first) / first) * 100 : null;
    // Intraday x-values are epoch milliseconds, not ISO dates — a session reads
    // in clock time, and showing 1757... where a date belongs is worse than
    // showing nothing.
    rd.textContent = intraday
      ? new Date(d[0]).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})
      : d[0];
    // a fund's book is billions; a share price is dollars
    const val = wrap.dataset.mode === "quarterly"
      ? fmtUSD(d[1]) : (d[1] == null ? "—" : "$" + d[1].toFixed(2));
    // Say what the percentage is measured against. Unlabelled beside a date it
    // reads as that day's move: ISRG closed 377.16 on 15 Sep, barely changed on
    // the day, and the bare "-4.4%" made a correct price look wrong.
    const span = intraday ? "today"
      : (wrap.querySelector(".rbtn.on")?.textContent || "").trim();
    const since = span === "today" ? "today"
                : (span && span !== "All") ? `vs ${span} ago`
                : (data[0] ? `since ${data[0][0]}` : "");
    rp.innerHTML = ch == null ? val
      : `${val} <span style="color:${ch>=0?"var(--buy)":"var(--sell)"};
      font-weight:500">${ch>=0?"+":""}${ch.toFixed(1)}%</span>` +
      (since ? ` <span class="sincelab">${esc(since)}</span>` : "");
  };
  show(data.length - 1);
  const move = ev => {
    const r = svg.getBoundingClientRect();
    const px = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left;
    show(Math.max(0, Math.min(data.length - 1,
      Math.round((px / r.width) * (data.length - 1)))));
  };
  svg.addEventListener("mousemove", move);
  svg.addEventListener("touchmove", move, {passive: true});
  svg.addEventListener("mouseleave", () => {
    cross.style.opacity = 0; dot.style.opacity = 0; show(data.length - 1);
  });
}

function initCharts(){
  document.querySelectorAll(".chartwrap").forEach(w => {
    drawChart(w);                 // draw what we have immediately
    loadFullSeries(w);            // then widen it if more history exists
  });
}

document.addEventListener("click", ev => {
  const pk = ev.target.closest("[data-pick]");
  if(pk){
    renderStock(pk.dataset.pick).then(() => initCharts());
    return;
  }
  const b = ev.target.closest(".rbtn");
  if(!b) return;
  const wrap = b.closest(".chartwrap");
  wrap.querySelectorAll(".rbtn").forEach(x => x.classList.remove("on"));
  b.classList.add("on");
  drawChart(wrap);
});

function perfCell(p){
  if(!p) return `<td class="num perf">—</td>`;
  const c = p.change_pct;
  const col = c > 0 ? "var(--buy)" : c < 0 ? "var(--sell)" : "var(--ink-3)";
  return `<td class="num perf" style="color:${col}">${c > 0 ? "+" : ""}${c.toFixed(1)}%
    <small>from $${p.then.toFixed(2)}</small></td>`;
}

function stockTable(rows, label){
  if(!rows.length) return `<p style="color:var(--ink-3);font-size:13px;margin:14px 0 0">
    No open-market ${label} in the window.</p>`;
  return `<table class="who-tbl">
    <thead><tr><th>${label === "buys" ? "Buyer" : "Seller"}</th><th class="num">Shares</th>
      <th class="num">Value</th><th class="num">Avg price</th>
      <th class="num">Since</th><th class="num">Last filed</th></tr></thead>
    <tbody>${rows.map(r => {
      const avg = r.shares ? r.value / r.shares : null;
      return `<tr>
      <td><div>${esc(r.who)}</div><div class="role">${esc([...r.roles].join(" · "))}</div></td>
      <td class="num">${r.shares.toLocaleString("en-US",{maximumFractionDigits:0})}</td>
      <td class="num">${fmtUSD(r.value) || "—"}</td>
      <td class="num">${avg ? "$" + avg.toFixed(2) : "—"}</td>
      ${perfCell(r.perf)}
      <td class="num" style="color:var(--ink-3)">${r.last}</td></tr>`;
    }).join("")}</tbody></table>`;
}

// "point 72", "Point-72" and "POINT72" are all the same fund to a person typing
const sq = s => (s || "").toLowerCase().replace(/[^a-z0-9]/g, "");

// sq() strips spaces, so a substring test runs straight across word
// boundaries: "gilbertcisneros" contains "tci", which offered a congressman
// as a match for the hedge fund TCI. Compare against the words instead.
function words(n){ return n.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean); }

function findMember(q){
  const k = sq(q);
  if(!k || k.length < 3) return null;
  const names = Object.keys(MEMBERS);
  const tok = w => names.find(n => words(n).some(w2 => w(w2)));
  return MEMBERS[names.find(n => sq(n) === k)]
      || MEMBERS[tok(w => w === k)]                       // a whole name word
      || MEMBERS[names.find(n => sq(n).startsWith(k))]
      || MEMBERS[k.length >= 4 ? tok(w => w.startsWith(k)) : undefined]
      || null;
}

function amtRange(t){
  const lo = t.amount_low, hi = t.amount_high;
  const f = v => v >= 1e6 ? "$" + (v/1e6).toFixed(v % 1e6 ? 2 : 0) + "M"
           : v >= 1e3 ? "$" + Math.round(v/1e3) + "K" : "$" + v;
  return hi ? `${f(lo)}–${f(hi)}` : `${f(lo)}+`;
}

function congressRows(list, showMember){
  return `<table class="who-tbl">
    <thead><tr>${showMember ? "<th>Member</th>" : ""}<th>Asset</th><th>Action</th>
      <th class="num">Amount</th><th class="num">Traded</th>
      <th class="num">Disclosed</th></tr></thead>
    <tbody>${list.map(t => {
      const lag = (Date.parse(t.filed_iso) - Date.parse(t.txn_iso)) / 864e5;
      const cls = t.action === "buy" ? "buy" : t.action === "sell" ? "sell" : "comp";
      return `<tr>
        ${showMember ? `<td>${esc(t.member)}<div class="role">${esc(t.state||"")}</div></td>` : ""}
        <td>${t.ticker ? `<b>${esc(t.ticker)}</b> ` : ""}<span class="role">${esc(t.asset)}</span>
          ${t.description ? `<div class="role">${esc(t.description)}</div>` : ""}</td>
        <td><span class="pill ${cls}">${esc(t.action)}${t.partial ? " (part)" : ""}</span></td>
        <td class="num">${amtRange(t)}</td>
        <td class="num">${t.txn_iso}</td>
        <td class="num" style="color:var(--ink-3)">${t.filed_iso}
          <small style="display:block">${isFinite(lag) ? Math.round(lag) + "d later" : ""}</small></td>
      </tr>`;
    }).join("")}</tbody></table>`;
}

function noteAlt(kind, name){
  const el = document.getElementById("stockpanel");
  el.insertAdjacentHTML("afterbegin",
    `<div class="recency" style="margin:0 0 12px">A ${kind === "member" ? "member of Congress"
      : "fund"} also matches this name — <b>${esc(name)}</b>. They are different people;
      search the full name to see the other.</div>`);
}

function renderMember(m){
  const el = document.getElementById("stockpanel");
  el.innerHTML = `<div class="stock">
    <h3>${esc(m.member)}</h3>
    <div class="sub">US House · ${esc(m.state || "")} · STOCK Act periodic transaction reports</div>
    <div class="kpis">
      <div class="kpi"><b>${m.trades.length}</b><span>Disclosed trades</span></div>
      <div class="kpi"><b style="color:var(--buy)">${m.buys}</b><span>Buys</span></div>
      <div class="kpi"><b style="color:var(--sell)">${m.sells}</b><span>Sells</span></div>
      <div class="kpi"><b>${m.last || "—"}</b><span>Most recent trade</span></div>
    </div>
    ${congressRows(m.trades, false)}
    <p class="lagnote">Amounts are disclosed only as <b>ranges</b> — the STOCK Act never
      requires an exact figure. Members have up to 30 days from notice and 45 from the
      trade to file, so the "disclosed" column shows how long each one actually took.
      Trades marked SP/JT belong to a spouse or joint account.</p>
  </div>`;
}

function findFund(q){
  const k = sq(q);
  if(!k) return null;
  const names = Object.keys(HOLDINGS);
  const person = n => sq(HOLDINGS[n].person);
  // Same boundary problem as findMember: an unanchored substring match on the
  // squashed string pairs queries with funds that merely contain the letters.
  const anyWord = (get, test) => names.find(n => words(get(n)).some(test));
  return HOLDINGS[names.find(n => sq(n) === k)]
      || HOLDINGS[names.find(n => person(n) === k)]
      || HOLDINGS[names.find(n => sq(n).startsWith(k))]
      || HOLDINGS[names.find(n => person(n).startsWith(k))]
      || HOLDINGS[anyWord(n => n, w => w === k)]
      || HOLDINGS[anyWord(n => HOLDINGS[n].person || "", w => w === k)]
      || HOLDINGS[k.length >= 4 ? anyWord(n => n, w => w.startsWith(k)) : undefined]
      || null;
}

function actionPill(a){
  const cls = /added|new/.test(a) ? "buy" : /trim|exit/.test(a) ? "sell" : "comp";
  return `<span class="pill ${cls}">${esc(a)}</span>`;
}

function nf(n){ return (n == null) ? "—" : Number(n).toLocaleString("en-US",{maximumFractionDigits:0}); }

function positionTip(r, f){
  const from = f.previous, to = f.period;
  const d = r.delta_shares, w = r.window;
  const dayAfter = from ? new Date(Date.parse(from + "T00:00:00Z") + 864e5)
                            .toISOString().slice(0,10) : null;
  const rows = [
    ["Shares held " + (from || "prior"), nf(r.prev_shares)],
    ["Shares held " + to, nf(r.shares)],
    ["Change", (d > 0 ? "+" : "") + nf(d) +
      (r.delta_pct == null ? "" : ` (${r.delta_pct > 0 ? "+" : ""}${r.delta_pct.toFixed(1)}%)`)],
    ["Value at " + to, fmtUSD(r.value) || "$0"],
  ];
  if(w) rows.push(
    ["Price range in window", `$${w.lo.toFixed(2)} – $${w.hi.toFixed(2)}`],
    ["Average close", `$${w.avg.toFixed(2)}`],
    ["Price now", `$${Number(w.now).toFixed(2)}`]);

  return `<div class="t">${esc(r.issuer)}${r.ticker ? " · " + esc(r.ticker) : ""}</div>
    <dl>${rows.map(([k,v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>
    <div class="warn"><b>The exact day is not disclosed.</b> A 13F is a snapshot of
      holdings on ${to}, not a trade log. This change happened somewhere between
      ${dayAfter || "the prior quarter end"} and ${to} — the filing never says when, at
      what price, or whether the position was bought and sold again inside the quarter.
      ${w ? `The stock traded $${w.lo.toFixed(2)}–$${w.hi.toFixed(2)} across those ${w.days} sessions.` : ""}
      Filed ${f.filed}.</div>`;
}

async function expandFund(cik, btn){
  btn.textContent = "Loading…"; btn.disabled = true;
  const d = await loadJSON(`data/fund-${cik}.json`);
  if(!d){
    btn.outerHTML = needsServer("this fund's full position list");
    return;
  }
  const cur = Object.values(HOLDINGS).find(h => h.cik === cik) || {};
  renderFund({...cur, ...d, _expanded: true});
}

function renderFund(f){
  const el = document.getElementById("stockpanel");
  const a = f.actions || {};
  const shown = f.rows.length, total = f.total_positions || f.rows.length;
  el.innerHTML = `<div class="stock">
    <h3>${f.logo && f.logo_sharp ? `<img class="av flogo-lg${f.logo_wide ? " wide" : ""}"
        src="${f.logo}" alt="" style="vertical-align:-12px;margin-right:10px">` : ""}${esc(f.fund)} · ${esc(f.person || "")}</h3>
    <div class="sub">13F holdings for ${f.period} · filed ${f.filed} ·
      compared with ${f.previous || "no prior quarter"} · CIK ${f.cik}</div>
    ${f.stale ? (() => {
      const yrs = (new Date(V.f13_period) - new Date(f.period)) / 31557600000;
      const age = yrs >= 1.5 ? `${Math.round(yrs)} years` : "several quarters";
      return `<div class="recency stale">This is <b>not current</b>, and it is not
      incomplete data — it is everything this CIK ever filed. Its last 13F-HR covers the
      quarter ended <b>${f.period}</b>, filed ${f.filed}, and nothing since: <b>${age}</b>
      behind the ${V.f13_period} everyone else reported. The manager stopped filing
      holdings reports under this CIK. Their Form 4, 144 and 13D activity, where it
      exists, is unaffected and still appears in the feed.</div>`; })() : ""}
    ${(f.history && f.history.length > 1) ? (() => {
      const h = f.history;
      const first = h[0], last = h[h.length - 1];
      const ch = (first[1] && isFinite(last[1]))
        ? ((last[1] - first[1]) / first[1]) * 100 : null;
      const col = ch == null ? "var(--ink-3)" : ch > 0 ? "var(--buy)" : "var(--sell)";
      return `<div class="pxbar" style="margin-top:16px">
          <div><div class="pxlab">Reported 13F value</div>
            <div class="pxnow">${fmtUSD(last[1])}</div></div>
          <div><div class="pxlab">Since ${first[0]}</div>
            <div class="pxdelta" style="color:${col}">${
              // Norges Bank's first quarter on file is zero, so there is no
              // percentage from it. col already handled null; this did not,
              // and the throw blanked the entire fund panel.
              ch == null ? "—" : (ch > 0 ? "+" : "") + ch.toFixed(1) + "%"
            }</div></div>
          <div><div class="pxlab">Quarters on file</div>
            <div class="pxdelta">${h.length}</div></div>
        </div>
        ${priceChart(h.map(r => [r[0], r[1]]), "hist-" + f.cik, "quarterly")}
        <p class="lagnote">Total value of long US equity positions at each quarter end,
          straight from the 13F. <b>This is not performance and not AUM</b> — it moves on
          inflows, redemptions and a single re-marked holding as readily as on returns,
          and it excludes shorts, bonds, and everything held outside 13(f) securities.</p>` ;
    })() : ""}
    <div class="kpis">
      <div class="kpi"><b>${total.toLocaleString()}</b><span>Positions</span></div>
      <div class="kpi"><b>${fmtUSD(f.total_value)}</b><span>Reported value</span></div>
      <div class="kpi"><b style="color:var(--buy)">${(a.added||0)+(a["new position"]||0)}</b><span>Added / new</span></div>
      <div class="kpi"><b style="color:var(--sell)">${(a.trimmed||0)+(a.exited||0)}</b><span>Trimmed / exited</span></div>
      <div class="kpi"><b>${a.held||0}</b><span>Unchanged</span></div>
    </div>
    <table class="who-tbl">
      <thead><tr><th>Security</th><th>Change</th><th class="num">Shares</th>
        <th class="num">Value</th><th class="num">vs prior qtr</th></tr></thead>
      <tbody>${f.rows.map(r => {
        const dp = r.delta_pct;
        const tk = r.ticker && TICKER_LIST.includes(r.ticker) ? r.ticker : null;
        // the security name is the control: underline on hover, expand in place
        const nameCell = tk
          ? `<button class="tklink" data-expand="${esc(tk)}">${esc(r.issuer)}</button>
             <div class="role">${esc(tk)}${r.class ? " · " + esc(r.class) : ""}</div>`
          : `${esc(r.issuer)}${r.class ? `<div class="role">${esc(r.class)}</div>` : ""}`;
        return `<tr class="holdrow"><td>${nameCell}</td>
          <td data-tip="${esc(positionTip(r, f))}" tabindex="0">${actionPill(r.action)}</td>
          <td class="num">${(r.shares||0).toLocaleString("en-US",{maximumFractionDigits:0})}</td>
          <td class="num">${fmtUSD(r.value) || "$0"}</td>
          <td class="num" style="color:${dp>0?"var(--buy)":dp<0?"var(--sell)":"var(--ink-3)"}">
            ${dp==null ? "new" : (dp>0?"+":"")+dp.toFixed(1)+"%"}</td></tr>
          <tr class="holdexp" hidden><td colspan="5"></td></tr>`;
      }).join("")}</tbody></table>
    ${shown < total ? `<div style="margin-top:14px">
      <button class="chip" id="more-${f.cik}" data-cik="${f.cik}">
        Show all ${total.toLocaleString()} positions</button></div>` : ""}
    <p class="lagnote">${shown < total
        ? `Showing the ${shown} largest of ${total.toLocaleString()} reported positions. `
        : ""}13F covers long US equity positions only — no shorts, no bonds, no
      non-US listings, and confidential treatment can hide holdings entirely. Filed
      ${f.filed} for the quarter ended ${f.period}, so it is already weeks stale by law.</p>
  </div>`;
}

function fundsTable(fh){
  return `<table class="who-tbl">
    <thead><tr><th>Fund</th><th>Change</th><th class="num">Shares</th>
      <th class="num">Value</th><th class="num">vs prior qtr</th>
      <th class="num">Window</th></tr></thead>
    <tbody>${fh.map(h => {
      const cls = /added|new/.test(h.action) ? "buy" : /trim|exit/.test(h.action) ? "sell" : "comp";
      const dp = h.delta_pct;
      return `<tr>
        <td>${esc(h.fund)}<div class="role">${esc(h.person||"")}${h.stale ? " · stale filing" : ""}</div></td>
        <td><span class="pill ${cls}">${esc(h.action)}</span></td>
        <td class="num">${(h.shares||0).toLocaleString("en-US",{maximumFractionDigits:0})}</td>
        <td class="num">${fmtUSD(h.value)||"$0"}</td>
        <td class="num" style="color:${dp>0?"var(--buy)":dp<0?"var(--sell)":"var(--ink-3)"}">
          ${dp==null ? "new" : (dp>0?"+":"")+dp.toFixed(1)+"%"}</td>
        <td class="num" style="color:var(--ink-3)">${h.previous || "?"} → ${h.period}
          <small style="display:block">filed ${h.filed}${h.rank ? ` · #${h.rank} of ${h.of}` : ""}</small></td>
        </tr>`;
    }).join("")}</tbody></table>`;
}

function priceBar(ticker, series, close, closeDate){
  const lq = QUOTES[ticker];
  if(!lq && !close) return "";
  const now = lq ? lq.price : close;
  const prev = lq && lq.prev_close ? lq.prev_close
             : (series && series.length > 1 ? series[series.length - 2][1] : null);
  const ch = prev ? (now - prev) / prev * 100 : null;
  const col = ch == null ? "var(--ink-3)" : ch > 0 ? "var(--buy)" : "var(--sell)";
  return `<div class="pxbar">
    <div><div class="pxlab">${lq ? `<span class="livedot"></span>Live price` : "Last close"}</div>
      <div class="pxnow">$${Number(now).toFixed(2)}</div></div>
    ${ch != null ? `<div><div class="pxlab">Change on day</div>
      <div class="pxdelta" style="color:${col}">${ch>0?"+":""}${ch.toFixed(2)}%</div></div>` : ""}
    ${close ? `<div><div class="pxlab">Close ${esc(closeDate||"")}</div>
      <div class="pxdelta">$${Number(close).toFixed(2)}</div></div>` : ""}
  </div>
  ${series && series.length > 1 ? priceChart(series, "lite-" + ticker, "daily", ticker) : ""}
  <p class="lagnote" style="margin-top:8px">
    ${lq ? `Quote from ${esc(QMETA.source || "Yahoo")}${lq.exchange ? " · " + esc(lq.exchange) : ""},
      fetched ${esc(QMETA.fetched || "")}. Not licensed real-time — exchange feeds are
      paid, so this is the closest free equivalent.` : "End-of-day close only."}
    ${series ? " History from Databento (licensed daily bars)." : ""}</p>`;
}

function lagBadge(days){
  if(days == null || !isFinite(days)) return "";
  const cls = days <= 5 ? "buy" : days <= 45 ? "act13d" : "sell";
  return `<span class="pill ${cls}" title="days between the trade and its disclosure">${days}d</span>`;
}

function dayGap(a, b){
  const x = Date.parse(a), y = Date.parse(b);
  return (isFinite(x) && isFinite(y)) ? Math.round((y - x) / 864e5) : null;
}

function peopleTab(e){
  const ins = e.insiders || [], cong = e.congress || [];
  if(!ins.length && !cong.length){
    return `<p class="lagnote">No individual filings for this issuer in the scanned
      window. Insiders only file when they trade, so silence is normal.</p>`;
  }
  const rows = [];
  ins.forEach(r => rows.push({
    who: person_display(r.who), sub: (r.roles || []).join(" · ") || "Section 16 insider",
    kind: "Insider", action: r.action, when: r.date, filed: r.filed,
    amount: r.value ? fmtUSD(r.value) : null,
    detail: `${(r.shares||0).toLocaleString()} sh${r.price ? " @ $" + r.price.toFixed(2) : ""}`,
    flag: r.rule_10b5_1 ? "10b5-1" : "", url: r.url,
  }));
  cong.forEach(c => rows.push({
    who: c.member, sub: c.state || "US House", kind: "Congress",
    action: c.action, when: c.txn_iso, filed: c.filed_iso,
    amount: amtRange(c), detail: c.description || c.asset, flag: "", url: null,
  }));
  rows.sort((a, b) => (b.when || "").localeCompare(a.when || ""));

  return `<table class="who-tbl">
    <thead><tr><th>Person</th><th>Type</th><th>Action</th><th class="num">Size</th>
      <th class="num">Traded</th><th class="num">Disclosed</th></tr></thead>
    <tbody>${rows.slice(0, 30).map(r => {
      const lag = dayGap(r.when, r.filed);
      const cls = r.action === "buy" ? "buy" : r.action === "sell" ? "sell" : "comp";
      return `<tr>
        <td>${r.url ? `<a href="${r.url}" target="_blank" rel="noopener">${esc(r.who)}</a>`
                    : esc(r.who)}<div class="role">${esc(r.sub)}</div></td>
        <td><span class="badge">${r.kind}</span></td>
        <td><span class="pill ${cls}">${esc(r.action)}</span>
          ${r.flag ? `<span class="tenb">${r.flag}</span>` : ""}</td>
        <td class="num">${r.amount || "—"}<small style="display:block;color:var(--ink-3)">
          ${esc((r.detail || "").slice(0, 40))}</small></td>
        <td class="num">${r.when || "—"}</td>
        <td class="num" style="color:var(--ink-3)">${r.filed || "—"} ${lagBadge(lag)}</td>
      </tr>`;
    }).join("")}</tbody></table>
    <p class="lagnote"><b>This is the fast lane.</b> Form 4 carries an exact trade date
      and execution price and lands a median of <b>2 days</b> later; congressional
      reports run about <b>27 days</b>. Compare that with the institutional tab, where a
      13F only says a position moved somewhere inside a three-month window and arrives
      45 or more days after it closed.</p>`;
}

function person_display(n){ return n || ""; }

function renderTickerLite(e){
  const el = document.getElementById("stockpanel");
  el.innerHTML = `<div class="stock">
    <h3>${esc(e.ticker)}${e.name ? " · " + esc(e.name) : ""}</h3>
    <div class="sub">Fund and congressional activity from data already indexed.
      Insider detail (Form 4 / 144) needs a one-off crawl of this issuer.</div>
    ${priceBar(e.ticker, e.series, e.close, e.close_date)}
    <div class="subtabs">
      <button class="stab on" data-pane="people">People
        <span class="n">${(e.insiders||[]).length + (e.congress||[]).length}</span></button>
      <button class="stab" data-pane="inst">Institutions
        <span class="n">${e.holders || 0}</span></button>
    </div>
    <div class="pane" data-pane="people">${peopleTab(e)}</div>
    <div class="pane" data-pane="inst" hidden>
    ${e.funds.length ? `<h4 class="sechead">Tracked funds holding this —
        ${e.holders} holder${e.holders === 1 ? "" : "s"}${e.holders > e.funds.length
          ? `, ${e.funds.length} largest shown` : ""}</h4>
      ${fundsTable(e.funds)}
      <p class="lagnote"><b>The "window" column is the answer to "when".</b> A 13F
        reports the position on the quarter-end date only, so a change is known to have
        happened inside that window and nowhere more precisely — not on the filing date,
        which is just the deadline. Rank shows where the position sits in that fund's
        book, so you can tell a conviction bet from a rounding error.</p>`
      : `<p class="lagnote">No tracked fund reported this in its latest 13F.</p>`}
    </div>
    <div class="nores" style="margin-top:18px">Full Section 16 detail for
      ${esc(e.ticker)} — exact dates, execution prices and Form 144 pre-sale
      notices — is indexed nightly and appears here once it lands.</div>
  </div>`;
}

// Heavy datasets live beside the page and load on demand. Over file:// the
// browser blocks these fetches, so the UI says so rather than silently
// showing nothing — run ./serve.sh and open the localhost URL.
const FILE_PROTO = location.protocol === "file:";
const _pending = {};

// The sharded data is 62 MB and rebuilt every cycle, so it is served from
// object storage rather than committed alongside the page — the repo would
// otherwise grow by megabytes a day. Locally it still comes from disk, so
// development needs no network and no bucket.
const DATA_BASE = (location.hostname === "localhost" ||
                   location.hostname === "127.0.0.1" || FILE_PROTO)
  ? "" : "https://storage.googleapis.com/virgil-web/";

async function loadJSON(path){
  if(_pending[path] !== undefined) return _pending[path];
  if(FILE_PROTO){ _pending[path] = null; return null; }
  try{
    const url = DATA_BASE ? DATA_BASE + path : path;
    const r = await fetch(url, DATA_BASE ? {} : {credentials: "same-origin"});
    _pending[path] = r.ok ? await r.json() : null;
  }catch(e){ _pending[path] = null; }
  return _pending[path];
}

function needsServer(what){
  return `<div class="nores">This page is open from a file, so the browser blocks
    loading ${what}. Run <code>./serve.sh</code> in ~/virgil and open the
    localhost link it prints — same page, with everything reachable.</div>`;
}

let _tidx = null;
async function ensureTickers(key){
  const shard = (key && key[0] && /[A-Z0-9]/.test(key[0])) ? key[0].toUpperCase() : "_";
  if(TICKERS.__loaded && TICKERS.__loaded[shard]) return TICKERS;
  if(!_tidx) _tidx = await loadJSON("data/tickers/_index.json");
  if(!_tidx) return TICKERS;
  const block = await loadJSON(`data/tickers/${shard}.json`);
  if(!block) return TICKERS;
  const d = {actions: _tidx.actions, funds: _tidx.funds, tickers: block};
  TICKERS.__loaded = TICKERS.__loaded || {};
  TICKERS.__loaded[shard] = true;
  // the file is normalised: fund metadata lives once in d.funds, and each
  // holder row references it by index
  const A = d.actions, F = d.funds;
  for(const [t, c] of Object.entries(d.tickers)){
    TICKERS[t] = {
      ticker: t, name: c.n, holders: c.h, insiders: c.i || [],
      series: c.s, close: c.px, close_date: c.pd,
      congress: c.c || [],
      funds: (c.f || []).map(r => {
        const f = F[r[0]];
        return {fund: f[0], person: f[1], period: f[2], previous: f[3],
                filed: f[4], stale: f[5], action: A[r[1]],
                shares: r[2], prev_shares: r[3], value: r[4],
                delta_pct: r[5], rank: r[6], of: r[7]};
      }),
    };
  }
  return TICKERS;
}

// ---------- following (per browser, no account) ----------
const FOLLOW_KEY = "virgil.following";
function following(){
  try { return new Set(JSON.parse(localStorage.getItem(FOLLOW_KEY) || "[]")); }
  catch(e){ return new Set(); }
}
function setFollowing(set){
  try { localStorage.setItem(FOLLOW_KEY, JSON.stringify([...set])); } catch(e){}
  paintFollowCount();
}
function paintFollowCount(){
  const n = following().size;
  const el = document.getElementById("followcount");
  if(el) el.textContent = n ? `${n} followed` : "";
}

function fbtn(id){
  const on = following().has(id);
  return `<button class="fbtn${on ? " on" : ""}" data-follow="${esc(id)}">
    ${on ? "Following" : "Follow"}</button>`;
}

function renderDiscover(){
  const el = document.getElementById("discoverbody");
  const fol = following();
  // A partnership has no principal to follow; its own name is its "person".
  const isFirmEntity = p => !p.person || p.person === p.firm ||
        /^(partnership|advisory arm)$/i.test(p.title || "");
  const allPeople = Object.values(PEOPLE).sort((a, b) => a.person.localeCompare(b.person));
  const people = allPeople.filter(p => !isFirmEntity(p));
  const firmEntities = allPeople.filter(isFirmEntity);
  const funds = Object.values(HOLDINGS).sort((a, b) => (b.total_value || 0) - (a.total_value || 0));
  const members = Object.values(MEMBERS).sort((a, b) =>
    (b.trades ? b.trades.length : 0) - (a.trades ? a.trades.length : 0)).slice(0, 60);

  // the card itself opens the entity; only the Follow control stops the click
  const card = (id, avatar, name, sub, meta, openKey) => `<div class="dcard"
      ${openKey ? `data-open="${esc(openKey)}" role="link" tabindex="0"` : ""}>
    ${avatar}<div class="body"><div class="nm">${esc(name)}</div>
      <div class="ti">${esc(sub || "")}</div>
      ${meta ? `<div class="mt">${meta}</div>` : ""}</div>${fbtn(id)}</div>`;

  // Optiver, Jane Street, Baillie Gifford and Vanguard Advisers are
  // partnerships with no single principal. They get the company mark, since
  // searching for "a headshot of Optiver" only ever finds someone else.
  const av = p => p.is_firm && p.firm_logo
      ? `<img class="av flogo-lg" src="${p.firm_logo}" alt="" loading="lazy">`
      : p.photo_local
      ? `<img class="av" src="${p.photo_local}" alt="" loading="lazy"
           data-fallback="${esc(p.person)}">`
      : monogram(p.person);

  el.innerHTML = `
    <p class="lagnote" style="margin:16px 0 0">Follow anyone here and their filings are
      pinned to the top of the feed. Stored in this browser only — no account, nothing
      leaves the machine.</p>

    <h2 class="dsec">Investors — ${people.length}</h2>
    <div class="dgrid">${people.map(p => card(
      "person:" + p.person, av(p), p.person, `${p.title} · ${p.firm}`,
      "", p.firm)).join("")}</div>

    ${firmEntities.length ? `<h2 class="dsec">Partnerships — no single principal</h2>
    <div class="dgrid">${firmEntities.map(p => card(
      "fund:" + p.firm,
      (p.logo ? `<img class="av flogo-lg" src="${p.logo}" alt="" loading="lazy">` : monogram(p.firm)),
      p.firm, "Partnership — the firm files, not a named person", "", p.firm)).join("")}</div>` : ""}

    <h2 class="dsec">Funds — ${funds.length}</h2>
    <div class="dgrid">${funds.map(f => card(
      "fund:" + f.fund,
      (f.logo_local ? `<img class="av flogo-lg" src="${f.logo_local}" alt="" loading="lazy"
          data-fallback="${esc(f.fund)}">` : monogram(f.fund)),
      f.fund, f.person,
      `${(f.total_positions||0).toLocaleString()} positions · ${fmtUSD(f.total_value)||"—"}`,
      f.fund)).join("")}</div>

    <h2 class="dsec">Members of Congress — ${Object.keys(MEMBERS).length}</h2>
    <div class="dgrid">${members.map(m => card(
      "member:" + m.member, monogram(m.member), m.member, m.state || "US House",
      `${m.trades.length} disclosed trades · last ${m.last || "—"}
       · <a href="#" data-open="${esc(m.member)}">Open</a>`)).join("")}</div>

    <h2 class="dsec">Companies — ${TICKER_LIST.length} searchable</h2>
    <p class="lagnote">Type any ticker in the search box to see who holds it, which
      insiders traded it, and whether Congress touched it.</p>`;
  paintFollowCount();
}

// A name can belong to more than one thing. TCI is a hedge fund we track and
// also the ticker for Transcontinental Realty Investors; Steve Cohen founded
// Point72 and sits in Congress. Showing one and telling the reader to "search
// the full name" for the other sent them somewhere that resolved right back
// here, so both are offered instead and the reader picks.
function chooserBar(opts, pick){
  if(opts.length < 2) return "";
  return `<div class="chooser">
    <span class="chooserlab">Matches ${opts.length}</span>
    ${opts.map(o => `<button class="pickbtn${o.kind === pick ? " on" : ""}"
        data-pick="${o.kind}">${esc(o.label)}<small>${esc(o.sub)}</small></button>`).join("")}
  </div>`;
}

async function renderStock(prefer){
  const el = document.getElementById("stockpanel");
  const key = query.trim().toUpperCase();
  if(!key){ el.innerHTML = ""; return; }
  if(TICKER_LIST.includes(key)) await ensureTickers(key);
  const d = STOCKS[key];
  const lite = (!d && TICKERS[key]) ? TICKERS[key] : null;
  const f = findFund(query), mem = findMember(query);

  const opts = [];
  if(d || lite) opts.push({kind:"stock", label:key,
    sub:(d && d.name) || (lite && lite.n) || "stock"});
  if(f) opts.push({kind:"fund", label:f.fund, sub:f.person || "fund"});
  if(mem) opts.push({kind:"member", label:mem.member, sub:"member of Congress"});
  const pick = (prefer && opts.some(o => o.kind === prefer)) ? prefer
             : (opts[0] ? opts[0].kind : null);
  const bar = () => { if(opts.length > 1)
      el.insertAdjacentHTML("afterbegin", chooserBar(opts, pick)); };

  if(pick === "fund" && f){ renderFund(f); bar(); return; }
  if(pick === "member" && mem){ renderMember(mem); bar(); return; }
  if(!d && lite){ renderTickerLite(lite); bar(); return; }
  if(!d && TICKER_LIST.includes(key) && FILE_PROTO){
    el.innerHTML = `<div class="stock"><h3>${esc(key)}</h3>
      <div class="sub">This ticker is indexed — the page just cannot load its data
        from a <code>file://</code> URL.</div>${needsServer("ticker detail")}</div>`;
    return;
  }
  if(!d){
    if(f){ renderFund(f); bar(); return; }
    if(mem){ renderMember(mem); bar(); return; }
    el.innerHTML = `<div class="stock"><h3>${esc(key)}</h3>
      <div class="sub" id="addsub">Not tracked yet.</div>
      <div id="addbox" style="margin-top:14px"></div></div>`;
    offerToAdd(key);
    return;
  }
  const _afterStock = bar;   // run once the panel below has been written
  const s = d.summary, buys = aggregateBy(d.trades, "buy"), sells = aggregateBy(d.trades, "sell");
  const disc = d.trades.filter(t => !t.rule_10b5_1).length;
  const r = d.recency || {};
  const stale = r.days_since_trade != null && r.days_since_trade > 30;
  const recencyNote = r.last_open_market_trade
    ? `<div class="recency ${stale ? "stale" : ""}">
         Last open-market insider trade <b>${r.last_open_market_trade}</b>
         — ${r.days_since_trade}&nbsp;days ago.
         ${stale ? "Nothing since." : ""}
         Most recent filing of any kind: ${r.last_filing_of_any_kind}
         (${r.days_since_filing}d ago).</div>`
    : "";
  const fh = d.funds || [];
  const fundsTbl = fh.length ? `
    <h4 class="sechead">Tracked funds holding this — latest 13F (${fh[0].period}, filed ${fh[0].filed})</h4>
    <table class="who-tbl"><thead><tr><th>Fund</th><th>Change</th>
      <th class="num">Shares</th><th class="num">Value</th><th class="num">vs prior qtr</th></tr></thead>
      <tbody>${fh.map(h => {
        const cls = /added|new/.test(h.action) ? "buy" : /trim|exit/.test(h.action) ? "sell" : "comp";
        const dp = h.delta_pct;
        return `<tr>
          <td><div>${esc(h.fund)}</div><div class="role">${esc(h.person||"")}</div></td>
          <td><span class="pill ${cls}">${esc(h.action)}</span></td>
          <td class="num">${(h.shares||0).toLocaleString("en-US",{maximumFractionDigits:0})}</td>
          <td class="num">${fmtUSD(h.value)||"—"}</td>
          <td class="num" style="color:${dp>0?"var(--buy)":dp<0?"var(--sell)":"var(--ink-3)"}">
            ${dp==null ? "new" : (dp>0?"+":"")+dp.toFixed(1)+"%"}</td></tr>`;
      }).join("")}</tbody></table>
    <p class="lagnote">13F is quarterly and lags 45–135 days by law. It shows long US equity
      positions only — no shorts, no bonds. A fund can have traded out of this entirely
      since the period end.</p>` : `
    <h4 class="sechead">Tracked funds holding this</h4>
    <p class="lagnote">None of the 28 tracked funds reported this security in their latest 13F.</p>`;
  el.innerHTML = `<div class="stock">
    <h3>${esc(d.ticker)} · ${esc(d.name)}</h3>
    <div class="sub">Every Section 16 insider of this issuer · CIK ${d.cik} ·
      window ${s.first || "—"} → ${s.last || "—"}</div>
    <div class="kpis">
      <div class="kpi"><b style="color:var(--buy)">${s.buys}</b><span>Insider buys</span></div>
      <div class="kpi"><b style="color:var(--sell)">${s.sells}</b><span>Insider sells</span></div>
      <div class="kpi"><b>${fmtUSD(s.buy_value) || "$0"}</b><span>Bought</span></div>
      <div class="kpi"><b>${fmtUSD(s.sell_value) || "$0"}</b><span>Sold</span></div>
      <div class="kpi"><b>${d.presales.length}</b><span>Pre-sale notices</span></div>
      <div class="kpi"><b>${disc}/${d.trades.length}</b><span>Discretionary</span></div>
    </div>
    ${d.price ? (() => {
      const p = {...d.price}, s2 = d.summary;
      const lq = QUOTES[d.ticker];
      if(lq && lq.price) p.now = lq.price;         // live quote beats the last close
      const ref = s2.sell_avg_price || s2.buy_avg_price;
      const ch = ref ? (p.now - ref) / ref * 100 : null;
      const col = ch == null ? "var(--ink-3)" : ch > 0 ? "var(--buy)" : "var(--sell)";
      return `<div class="pxbar">
        <div><div class="pxlab">${lq ? `<span class="livedot"></span>Live price` : "Last close"}</div>
          <div class="pxnow">$${(p.now||0).toFixed(2)}</div></div>
        ${ref ? `<div><div class="pxlab">Insiders transacted at</div>
          <div class="pxdelta">$${ref.toFixed(2)}</div></div>
        <div><div class="pxlab">Since those trades</div>
          <div class="pxdelta" style="color:${col}">${ch>0?"+":""}${ch.toFixed(1)}%</div></div>` : ""}
      </div>
      ${priceChart(p.series, "full-" + d.ticker, "daily", d.ticker)}
      <p class="lagnote" style="margin-top:8px">History from ${esc(p.source||"")}.
        ${lq ? `Live quote from ${esc(QMETA.source||"Yahoo")}${lq.exchange ? " · " + esc(lq.exchange) : ""}
          ${lq.delay_s ? ", delayed " + Math.round(lq.delay_s/60) + " min" : ""},
          fetched ${esc(QMETA.fetched||"")}.` : ""} Daily closes —
        deliberately not a licensed real-time feed, because these filings lag 2 to 135 days
        and tick data would add precision the input cannot use.</p>`;
    })() : ""}
    ${recencyNote}
    ${stockTable(sells, "sells")}
    ${stockTable(buys, "buys")}
    ${fundsTbl}
    ${(CONGTKR[key] && CONGTKR[key].length) ? `
      <h4 class="sechead">Congress traded this — House STOCK Act filings</h4>
      ${congressRows(CONGTKR[key], true)}` : ""}
    ${d.presales.length ? `<h4 style="font:600 11px Inter,sans-serif;letter-spacing:.07em;
        text-transform:uppercase;color:var(--ink-3);margin:22px 0 0">Intent to sell (Form 144)</h4>
      <table class="who-tbl"><tbody>${d.presales.slice(0,6).map(p=>`<tr>
        <td>${esc(p.who)}<div class="role">${esc((p.roles||[]).join(" · "))}</div></td>
        <td class="num">${(p.units||0).toLocaleString("en-US",{maximumFractionDigits:0})}</td>
        <td class="num">${fmtUSD(p.value)||"—"}</td>
        <td class="num" style="color:var(--ink-3)">filed ${p.filed}</td></tr>`).join("")}</tbody></table>` : ""}
  </div>`;
  _afterStock();
}

function matches(e){
  if(!showComp && e.category === "comp") return false;
  if(query){
    const q = query.toLowerCase();
    const hay = `${e.ticker||""} ${e.issuer||""} ${e.actor||""} ${e.watch_label||""}`.toLowerCase();
    if(!hay.includes(q)) return false;
  }
  if(filter === "all") return true;
  if(filter === "buy")  return e.action === "buy";
  if(filter === "sell") return e.action === "sell";
  return e.type === filter;
}

function pill(e){
  if(e.type === "presale") return `<span class="pill presale">Intends to sell</span>`;
  if(e.type === "activist"){
    const a = e.action || "";
    const cls = a === "raised stake" ? "buy" : a === "trimmed stake" ? "sell" : "act13d";
    const label = a === "raised stake" ? "Raised" : a === "trimmed stake" ? "Trimmed"
                : a === "new >5% stake" ? "New >5%" : "13D/A";
    return `<span class="pill ${cls}">${label}</span>`;
  }
  if(e.action === "buy")  return `<span class="pill buy">Buy</span>`;
  if(e.action === "sell") return `<span class="pill sell">Sell</span>`;
  return `<span class="pill comp">${e.action}</span>`;
}

// The chips only ever filtered the signal feed, but Activist Stakes and Cluster
// Buys sit above it and ignore them — so clicking a chip changed nothing you
// could see, because the feed is below the fold. Collapse the summary sections
// when a filter is on, and say in words what the filter means.
function applyFilterView(){
  const narrow = filter !== "all";
  const lg = document.getElementById("filterlegend");
  if(lg){
    lg.textContent = narrow ? (LEGEND[filter] || "") : "";
    lg.hidden = !narrow;
  }

}

function isFollowed(e, fol){
  return fol.has("person:" + (e.principal || "")) ||
         fol.has("fund:" + (e.watch_label || "")) ||
         fol.has("member:" + (e.member || "")) ||
         (e.ticker && fol.has("stock:" + e.ticker));
}

function renderFeed(){
  const fol = following();
  const head = document.getElementById("feedhead");
  const empty = document.getElementById("feedempty");
  const feed = document.getElementById("feed");
  const rows = EVENTS.filter(matches).filter(e => isFollowed(e, fol));

  if(!fol.size){
    head.hidden = true; feed.innerHTML = "";
    empty.hidden = false;
    empty.innerHTML = `<div class="empty-state">
      <h3>Your feed is empty</h3>
      <p>Follow an investor, a fund or a stock and their filings appear here —
        nothing else. Everything in the market stays one tab over in
        <a href="#" data-gotab="discover">Discover</a>.</p>
      <div class="starter">${["Bill Ackman","Warren Buffett","Nancy Pelosi","Carl Icahn"]
        .filter(n => PEOPLE[n] || MEMBERS[n])
        .map(n => `<button class="fbtn" data-follow="${PEOPLE[n] ? "person:" : "member:"}${esc(n)}">
          Follow ${esc(n)}</button>`).join("")}</div>
    </div>`;
    return;
  }
  empty.hidden = true; head.hidden = false;
  document.getElementById("feedcount").textContent =
    `— ${rows.length} filing${rows.length === 1 ? "" : "s"} from ${fol.size} followed`;
  feed.innerHTML = rows.length ? rows.slice(0, 250).map(rowHTML).join("")
    : `<div class="empty">Nothing new from who you follow under this filter.</div>`;
}

// One row renderer shared by Feed and Discover. The ticker is a real control:
// hovering underlines it, clicking expands the company inline underneath rather
// than throwing the reader somewhere else.
function rowHTML(e){
  const who = e.actor || e.watch_label;
  const roles = (e.actor_roles && e.actor_roles.length) ? e.actor_roles.join(" · ")
              : (e.principal_title ? e.principal_title + ", " + e.watch_label : e.watch_label);
  const amt = fmtUSD(e.value);
  const isAct = e.type === "activist";
  const dpp = e.delta_percent;
  const deltaChip = (dpp != null && Math.abs(dpp) >= 0.05)
    ? `<span class="tenb" style="color:${dpp>0?"var(--buy)":"var(--sell)"};border-color:currentColor">
         ${dpp>0?"+":""}${dpp.toFixed(1)} pp</span>` : "";
  const tk = e.ticker || "";
  const known = tk && TICKER_LIST.includes(tk);
  const label = tk || (e.issuer || "").slice(0, 30) || "—";
  const secCell = known
    ? `<button class="tklink" data-expand="${esc(tk)}">${esc(label)}</button>`
    : `<span class="tk">${esc(label)}</span>`;
  return `<div class="rowwrap" data-row="${esc(tk)}">
    <div class="row">
      <div class="when">${e.filed}<br><span style="opacity:.7">${ago(e.filed)}</span></div>
      <div class="who"><div class="nm">${esc(who)}</div><div class="rl">${esc(roles)}</div></div>
      <div class="act">${pill(e)}${deltaChip}${e.rule_10b5_1 ? '<span class="tenb">10b5-1</span>' : ""}</div>
      <div class="sec">${secCell}
        <div class="is">${isAct ? (e.amendment ? "Amendment #" + e.amendment : "Schedule 13D")
                                : (e.ticker ? esc(e.issuer||"") : esc(e.security||""))}</div></div>
      <div class="amt">${isAct
          ? (e.percent != null ? `${e.percent.toFixed(1)}%<span class="sh">of class</span>`
                               : '<span class="muted">—</span>')
          : (amt ? amt : '<span class="muted">—</span>')}
        <span class="sh">${fmtSh(e.shares)}</span></div>
      <a class="filing" href="${e.url}" target="_blank" rel="noopener"
         title="open the filing on sec.gov">filing</a>
    </div>
    <div class="expand" hidden></div>
  </div>`;
}

function render(){
  renderFeed();
  const rows = EVENTS.filter(matches);
  const feed = document.getElementById("allfeed");
  if(!rows.length){
    feed.innerHTML = `<div class="empty">No filings match this filter yet.<br>
      The poller writes new events as they land on EDGAR.</div>`;
  } else {
    feed.innerHTML = rows.slice(0, 250).map(rowHTML).join("");
  }

  const n = f => EVENTS.filter(e => (showComp || e.category !== "comp") &&
      (f === "buy" ? e.action === "buy" : f === "sell" ? e.action === "sell" : e.type === f)).length;
  document.getElementById("c-buy").textContent = n("buy");
  document.getElementById("c-sell").textContent = n("sell");
  document.getElementById("c-activist").textContent = n("activist");
  document.getElementById("c-insider").textContent = n("insider");
  document.getElementById("c-fund").textContent = n("fund_trade");
  document.getElementById("c-presale").textContent = n("presale");
  document.getElementById("c-comp").textContent = EVENTS.filter(e => e.category === "comp").length;
}

function renderStakes(){
  const el = document.getElementById("stakes");
  const latest = new Map();     // one row per (filer, target): the newest amendment
  EVENTS.filter(e => e.type === "activist" && e.percent != null).forEach(e => {
    const k = e.watch_cik + ":" + e.issuer_cik;
    const cur = latest.get(k);
    if(!cur || e.filed > cur.filed) latest.set(k, e);
  });
  const rows = [...latest.values()].sort((a,b) => b.filed.localeCompare(a.filed));
  if(!rows.length){
    el.innerHTML = `<div class="empty" style="border:1px solid var(--rule);border-radius:10px">
      No 13D stakes parsed yet.</div>`;
    return;
  }
  el.innerHTML = rows.slice(0, 10).map(e => {
    const d = e.delta_percent;
    const arrow = (d == null || Math.abs(d) < 0.05) ? ""
      : `<span style="color:${d>0?"var(--buy)":"var(--sell)"}">${d>0?"▲":"▼"} ${Math.abs(d).toFixed(1)} pp</span>`;
    return `<a class="cl" href="${e.url}" target="_blank" rel="noopener"
              style="grid-template-columns:auto 1fr auto">
      <div><span class="count" style="background:var(--flag-bg);color:var(--flag);width:auto;
        padding:0 11px;border-radius:14px;font-size:13px">${e.percent.toFixed(1)}%</span></div>
      <div><div class="tk">${esc(e.issuer)}</div>
        <div class="co">${esc(e.actor)}${e.amendment ? " · amendment #" + e.amendment : ""} · ${e.filed}</div>
        ${e.purpose ? `<div class="ppl" style="margin-top:6px;color:var(--ink-3)">${esc(e.purpose.slice(0,190))}…</div>` : ""}</div>
      <div class="tot"><b>${fmtSh(e.shares)}</b><span>${arrow || "&nbsp;"}</span></div>
    </a>`;
  }).join("");
}

function renderClusters(){
  const el = document.getElementById("clusters");
  if(!CLUSTERS.length){
    el.innerHTML = `<div class="empty" style="border:1px solid var(--rule);border-radius:10px">
      No cluster buys in the scanned window.<br>
      Genuine clusters are rare — that is what makes them worth watching.</div>`;
    return;
  }
  el.innerHTML = CLUSTERS.slice(0, 12).map(c => {
    const names = c.buyers.slice(0, 5).map(b =>
      `${esc(b.name)}<span style="color:var(--ink-3)">${b.roles && b.roles.length
        ? " · " + esc(b.roles[0]) : ""} · ${fmtUSD(b.value)}</span>`).join("<br>");
    const more = c.buyers.length > 5 ? `<br><span style="color:var(--ink-3)">+${c.buyers.length - 5} more</span>` : "";
    return `<a class="cl" href="${c.url}" target="_blank" rel="noopener">
      <div><span class="count">${c.insiders}</span></div>
      <div><div class="tk">${c.ticker || c.issuer}</div>
        <div class="co">${c.ticker ? c.issuer : ""}</div>
        <div class="ppl" style="margin-top:6px">${names}${more}</div></div>
      <div class="tot"><b>${fmtUSD(c.value)}</b>
        <span>${fmtSh(c.shares)}</span>
        <span style="display:block">${c.first_filed === c.last_filed ? isoDay(c.first_filed)
          : isoDay(c.first_filed) + " – " + isoDay(c.last_filed)}</span></div>
    </a>`;
  }).join("");
}

function renderPeople(){
  const el = document.getElementById("people");
  const list = Object.values(PEOPLE);
  document.getElementById("pcount").textContent = "— " + list.length + " principals";
  el.innerHTML = list.map(p => {
    const last = EVENTS.find(e => e.watch_cik === p.cik);
    // assets are already face-cropped squares, so no object-position guessing
    const av = p.photo_local
      ? `<img class="av" src="${p.photo_local}" alt="" loading="lazy"
           data-fallback="${esc(p.person)}">`
      : monogram(p.person);
    return `<a class="p" href="#${encodeURIComponent(p.firm)}" data-open="${esc(p.firm)}">
      ${av}
      <div class="body">
        <div class="nm">${p.person}</div>
        <div class="ti">${p.title}</div>
        <div class="fm">${p.logo ? `<img class="flogo" src="${p.logo}" alt="" loading="lazy">` : ""}
          <span class="badge">${p.firm}</span>
          ${last ? `<span>${ago(last.filed)}</span>` : `<span style="opacity:.6">no recent filing</span>`}</div>
      </div></a>`;
  }).join("");
}

function renderFirms(){
  document.getElementById("firms").innerHTML = Object.entries(FIRMS).map(([tkr, f]) => {
    const c = EVENTS.filter(e => e.watch_cik === f.cik).length;
    return `<a class="p" href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${f.cik}&type=4&dateb=&owner=include&count=40"
               target="_blank" rel="noopener">
      ${(LOGOS[tkr] && LOGOS[tkr].logo && LOGOS[tkr].sharp)
          ? `<img class="av flogo-lg${LOGOS[tkr].wide ? " wide" : ""}"
               src="${LOGOS[tkr].logo}" alt="${esc(tkr)} logo" loading="lazy"
               data-fallback="${esc(tkr)}">`
          : tickerMark(tkr)}
      <div class="body"><div class="nm">${tkr}</div>
      <div class="ti">${f.name}</div>
      <div class="fm"><span class="badge">${c} event${c===1?"":"s"}</span></div></div></a>`;
  }).join("");
}

// portraits occasionally 404 from Wikipedia; fall back without inline handlers
addEventListener("error", ev => {
  const img = ev.target;
  if(img && img.tagName === "IMG" && img.dataset.fallback){
    img.outerHTML = monogram(img.dataset.fallback);
  }
}, true);

// Expand a company inline, beneath the row that mentions it. Deliberately not a
// navigation: the reader keeps their place in the list.
// Same behaviour for a holdings table, where the panel has to live in a
// sibling row rather than a sibling div.
async function expandHolding(tk, tr){
  const next = tr.nextElementSibling;
  if(!next || !next.classList.contains("holdexp")) return;
  const cell = next.firstElementChild;
  if(!next.hidden){
    next.hidden = true; tr.classList.remove("open"); return;
  }
  tr.closest("tbody").querySelectorAll("tr.holdexp").forEach(r => r.hidden = true);
  tr.closest("tbody").querySelectorAll("tr.holdrow.open").forEach(r => r.classList.remove("open"));
  next.hidden = false; tr.classList.add("open");
  cell.innerHTML = `<div class="expand" style="padding:14px 0"><p class="lagnote">Loading ${esc(tk)}…</p></div>`;
  await ensureTickers(tk);
  const e = TICKERS[tk];
  cell.innerHTML = `<div class="expand" style="padding:6px 0 18px">${
    e ? companyPanel(e, tk) : (FILE_PROTO ? needsServer("company detail")
        : `<p class="lagnote">No indexed data for ${esc(tk)}.</p>`)}</div>`;
  initCharts();
}

async function expandRow(tk, wrap){
  const box = wrap.querySelector(".expand");
  if(!wrap.classList.contains("open")){
    document.querySelectorAll(".rowwrap.open").forEach(w => {
      if(w !== wrap){ w.classList.remove("open"); w.querySelector(".expand").hidden = true; }
    });
  }
  if(wrap.classList.contains("open")){
    wrap.classList.remove("open"); box.hidden = true; return;
  }
  wrap.classList.add("open"); box.hidden = false;
  box.innerHTML = `<p class="lagnote" style="padding-top:14px">Loading ${esc(tk)}…</p>`;
  await ensureTickers(tk);
  const e = TICKERS[tk];
  if(!e){
    box.innerHTML = FILE_PROTO ? needsServer("company detail")
      : `<p class="lagnote" style="padding-top:14px">No indexed data for ${esc(tk)}.</p>`;
    return;
  }
  box.innerHTML = companyPanel(e, tk);
  initCharts();
}


// ---- adding an investor who is not tracked yet -------------------------
// The page is static, so name lookup runs against a shipped index of everyone
// who has filed a 13F, 13D or 13G in the last two years (www.sec.gov sends no
// CORS headers, so the browser cannot ask EDGAR itself). Picking a candidate
// POSTs to /api/add and the poller ingests their filings on its next cycle.
const FILERS = {__loaded:{}};
// Labels differ from registered names — "Bridgewater" here is "Bridgewater
// Associates, LP" on EDGAR — so offering to add one would silently duplicate
// a fund already present. Identity is the CIK.
const TRACKED = /*__TRACKED__*/{};
let ALIASES = null;

async function loadAliases(){
  if(ALIASES) return ALIASES;
  ALIASES = (await loadJSON("data/aliases.json")) || {};
  return ALIASES;
}

function aliasFor(q){
  // People file under their fund, not their own name: "cathie wood" is ARK.
  const key = q.toLowerCase().replace(/[^a-z ]/g, "").trim();
  if(!ALIASES) return null;
  if(ALIASES[key]) return {...ALIASES[key], typed: key};
  const terms = key.split(/\s+/).filter(Boolean);
  for(const k in ALIASES){
    const kt = k.split(/\s+/);
    if(kt.length === terms.length &&
       kt.every((w, i) => w === terms[i] || nearMiss(w, terms[i]) ||
                          w.startsWith(terms[i]) || terms[i].startsWith(w)))
      return {...ALIASES[k], typed: k};
  }
  return null;
}

async function loadFilers(ch){
  const k = /[A-Z]/.test(ch) ? ch : "_";
  if(FILERS.__loaded[k]) return;
  FILERS.__loaded[k] = true;
  const blk = await loadJSON(`data/filers/${k}.json`);
  if(blk) Object.assign(FILERS, blk);
}

function nearMiss(a, b){
  // one insertion, deletion or substitution apart
  if(Math.abs(a.length - b.length) > 1) return false;
  let i = 0, j = 0, slips = 0;
  while(i < a.length && j < b.length){
    if(a[i] === b[j]){ i++; j++; continue; }
    if(++slips > 1) return false;
    if(a.length > b.length) i++;
    else if(b.length > a.length) j++;
    else { i++; j++; }
  }
  return slips + (a.length - i) + (b.length - j) <= 1;
}

function scoreFiler(name, terms){
  // Token-prefix matching, not substring: "ark invest" must not match
  // "BenchmARK INVESTment Advisors".
  const toks = name.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  let hit = 0;
  for(const t of terms){
    if(toks.some(w => w === t)) hit += 2;
    else if(toks.some(w => w.startsWith(t))) hit += 1;
    // "woods" should still reach "Wood": accept the typed word being the
    // longer one, and tolerate a single slipped character on longer words.
    else if(t.length >= 4 && toks.some(w => t.startsWith(w) && w.length >= 3)) hit += 1;
    else if(t.length >= 5 && toks.some(w => nearMiss(w, t))) hit += 1;
    else return 0;
  }
  return hit + (toks.length <= terms.length + 2 ? 1 : 0);
}

function searchFilers(q){
  const terms = q.toLowerCase().split(/[^a-z0-9]+/).filter(t => t.length > 1);
  if(!terms.length) return [];
  const out = [];
  for(const cik in FILERS){
    if(cik === "__loaded") continue;
    const r = FILERS[cik];
    const sc = scoreFiler(r.n, terms);
    if(sc) out.push({cik, name:r.n, forms:r.f, last:r.d, score:sc});
  }
  out.sort((a,b) => b.score - a.score || (b.last||"").localeCompare(a.last||""));
  return out.slice(0, 8);
}

function formLabel(f){
  const m = {"13F-HR":"quarterly holdings", "SC 13D":"activist stakes",
             "SC 13G":"passive stakes", "SCHEDULE 13D":"activist stakes",
             "SCHEDULE 13G":"passive stakes"};
  return [...new Set((f||[]).map(x => m[x] || x))].join(" · ");
}

// "Added" followed by silence is not a status. Ingesting reads EDGAR, pulls a
// 13F and fetches a logo, which takes minutes — so say what is happening, and
// keep checking until the entity actually shows up rather than leaving the
// reader to guess whether it worked.
async function watchForEntity(name, cik, sub, kicked){
  const started = Date.now();
  const say = t => { if(sub) sub.innerHTML = t; };
  // Be specific about the wait. "A few minutes" with no further word is what
  // made this feel broken; the reader should know roughly how long, and see it
  // counting.
  say(kicked
    ? `<span class="spin"></span> Adding <b>${esc(name)}</b> — reading their
       filings from EDGAR. Usually about <b>3 minutes</b>; this page updates
       itself when they land.`
    : `<span class="spin"></span> <b>${esc(name)}</b> is queued. The poller
       picks it up within 5 minutes, then takes about 3 more.`);
  for(let i = 0; i < 80; i++){
    await new Promise(r => setTimeout(r, 15000));
    try{
      const base = DATA_BASE || "";
      const r = await fetch(`${base}data/live-meta.json?t=${Date.now()}`,
                            {cache: "no-store"});
      if(r.ok){
        const m = await r.json();
        if(m.built && m.built !== (META.generated || "")){
          say(`<b>${esc(name)}</b> is on Virgil now — reload to see them in
               Discover and on any stock they have traded.`);
          return;
        }
      }
    }catch(e){ /* keep waiting */ }
    const mins = Math.round((Date.now() - started) / 60000);
    if(mins >= 1) say(`<span class="spin"></span> Still adding <b>${esc(name)}</b> —
      pulling their holdings and rebuilding the page. <b>${mins} min</b> so far,
      usually about 3.`);
  }
  say(`<b>${esc(name)}</b> is queued but has not appeared yet. The poller runs
       every few minutes; they will show up on its next pass.`);
}

async function offerToAdd(q){
  const box = document.getElementById("addbox");
  const sub = document.getElementById("addsub");
  if(!box) return;
  box.innerHTML = `<div class="lagnote">Checking SEC EDGAR…</div>`;
  const terms = q.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  for(const t of terms) await loadFilers(t[0].toUpperCase());
  await loadAliases();
  let hits = searchFilers(q);

  // A person almost never files under their own name, so if we know whose fund
  // this is, put that fund at the top rather than reporting nothing found.
  const al = aliasFor(q);
  if(al && !hits.some(h => h.cik === al.cik)){
    await loadFilers((al.filer || "?")[0].toUpperCase());
    const rec = FILERS[al.cik];
    hits = [{cik: al.cik, name: al.filer, forms: (rec && rec.f) || ["13F-HR"],
             last: (rec && rec.d) || "", via: "alias"}].concat(hits);
  }

  if(!hits.length){
    sub.textContent = "Not tracked yet.";
    box.innerHTML = `<div class="nores">
      <b>No SEC filings found for “${esc(q)}”.</b><br><br>
      Nobody by that name has filed a 13F, 13D or 13G in the last two years.
      Private investors who manage under $100M, or who trade only their own
      money, have no filing obligation — so there is genuinely nothing public
      to track.<br><br>
      Investors usually file under their firm rather than their own name: for
      Cathie Wood, search <b>ARK Investment Management</b>.</div>`;
    return;
  }

  const known = hits.filter(h => TRACKED[h.cik]);
  if(known.length){
    const k = known[0];
    sub.textContent = "Already tracked.";
    box.innerHTML = `<div class="nores"><b>${esc(k.name)}</b> is already on Virgil
      as <b>${esc(TRACKED[k.cik])}</b>.
      <button class="fbtn" style="margin-left:8px" data-open="${esc(TRACKED[k.cik])}"
        data-prefer="fund">Open it</button></div>`;
    return;
  }

  const one = hits.length === 1;
  sub.textContent = one
    ? "Not tracked yet — found on SEC EDGAR."
    : `Not tracked yet — ${hits.length} filers match that name.`;
  const aliasNote = al ? `<p class="lagnote" style="margin:0 0 10px">
      Investors file under their fund, not their own name —
      <b>${esc(q)}</b> files as <b>${esc(al.filer)}</b>.</p>` : "";
  box.innerHTML = aliasNote +
    ((one || al) ? "" : `<p class="lagnote" style="margin:0 0 10px">
        More than one filer matches. Which one did you mean?</p>`) +
    hits.map(h => `<div class="dcard addcand" data-cik="${h.cik}"
        data-name="${esc(h.name)}">
        <div class="body">
          <div class="nm">${esc(h.name)}</div>
          <div class="sb">${formLabel(h.forms)}</div>
          <div class="mt">CIK ${h.cik} · last filed ${h.last || "—"}</div>
        </div>
        <button class="fbtn" type="button">Add</button>
      </div>`).join("");

  const viaPerson = al ? q.replace(/\s+/g, " ").trim() : "";
  box.querySelectorAll(".addcand").forEach(card => {
    card.querySelector("button").addEventListener("click", async () => {
      const btn = card.querySelector("button");
      btn.disabled = true; btn.textContent = "Adding…";
      try{
        const r = await fetch("/api/add", {method:"POST",
          headers:{"Content-Type":"application/json"},
          body: JSON.stringify({cik: card.dataset.cik, name: card.dataset.name,
                                label: card.dataset.name,
                                person: card.dataset.cik === (al && al.cik)
                                        ? viaPerson : ""})});
        const j = await r.json().catch(() => ({}));
        if(!r.ok || !j.status) throw new Error(j.error || `HTTP ${r.status}`);
        btn.textContent = j.status === "queued" ? "Added" : "Already added";
        watchForEntity(card.dataset.name, card.dataset.cik, sub, j.started);
      }catch(e){
        btn.disabled = false; btn.textContent = "Add";
        sub.textContent = "Could not queue that just now — the request did not " +
          "go through. Try again in a moment.";
      }
    });
  });
}

function companyPanel(e, tk){
  const fol = following(), watching = fol.has("stock:" + tk);
  const held = (e.funds || []).slice(0, 5);
  const ins = (e.insiders || []).slice(0, 5);
  return `
    <div class="exp-head">
      <span class="exp-name">${esc(tk)}</span>
      <span class="exp-sub">${esc(e.name || "")}</span>
      <span class="exp-actions">
        <button class="fbtn${watching ? " on" : ""}" data-follow="stock:${esc(tk)}">
          ${watching ? "Watching" : "Watch"}</button>
        <button class="fbtn" data-open="${esc(tk)}">Full page</button>
      </span>
    </div>
    ${priceBar(tk, e.series, e.close, e.close_date)}
    <div class="exp-grid">
      <div>
        <h4 class="sechead">People trading it — ${(e.insiders||[]).length}</h4>
        ${ins.length ? `<table class="who-tbl"><tbody>${ins.map(r => `<tr>
            <td>${esc(r.who)}<div class="role">${esc((r.roles||[]).join(" · "))}</div></td>
            <td><span class="pill ${r.action === "buy" ? "buy" : "sell"}">${esc(r.action)}</span></td>
            <td class="num">${fmtUSD(r.value) || "—"}</td>
            <td class="num" style="color:var(--ink-3)">${r.date || ""}</td></tr>`).join("")}
          </tbody></table>`
          : `<p class="lagnote">No insider filed on this issuer in the scanned window.</p>`}
      </div>
      <div>
        <h4 class="sechead">Funds holding it — ${e.holders || 0}</h4>
        ${held.length ? `<table class="who-tbl"><tbody>${held.map(h => `<tr>
            <td>${esc(h.fund)}<div class="role">${esc(h.person || "")}</div></td>
            <td><span class="pill ${/added|new/.test(h.action) ? "buy"
                : /trim|exit/.test(h.action) ? "sell" : "comp"}">${esc(h.action)}</span></td>
            <td class="num">${fmtUSD(h.value) || "—"}</td></tr>`).join("")}
          </tbody></table>`
          : `<p class="lagnote">No tracked fund reported this in its latest 13F.</p>`}
      </div>
    </div>`;
}

document.addEventListener("click", ev => {
  const fb = ev.target.closest("[data-follow]");
  if(fb){
    const set = following(), id = fb.dataset.follow;
    set.has(id) ? set.delete(id) : set.add(id);
    setFollowing(set);
    fb.classList.toggle("on");
    const isStock = id.startsWith("stock:");
    fb.textContent = set.has(id) ? (isStock ? "Watching" : "Following")
                                 : (isStock ? "Watch" : "Follow");
    renderFeed();
    return;
  }
  const st = ev.target.closest(".stab");
  if(st){
    const box = st.closest(".stock");
    box.querySelectorAll(".stab").forEach(x => x.classList.toggle("on", x === st));
    box.querySelectorAll(".pane").forEach(pn =>
      pn.hidden = pn.dataset.pane !== st.dataset.pane);
    initCharts();
    return;
  }
  const xp = ev.target.closest("[data-expand]");
  if(xp){
    ev.preventDefault();
    const wrap = xp.closest(".rowwrap");
    if(wrap){ expandRow(xp.dataset.expand, wrap); return; }
    const tr = xp.closest("tr.holdrow");
    if(tr){ expandHolding(xp.dataset.expand, tr); return; }
  }
  const goto = ev.target.closest("[data-gotab]");
  if(goto){
    ev.preventDefault();
    document.querySelector(`.tab[data-tab="${goto.dataset.gotab}"]`).click();
    return;
  }
  const tab = ev.target.closest(".tab");
  if(tab){
    document.querySelectorAll(".tab").forEach(t => t.classList.toggle("on", t === tab));
    const wantDiscover = tab.dataset.tab === "discover";
    document.getElementById("discover").hidden = !wantDiscover;
    document.getElementById("feedview").hidden = wantDiscover;
    document.getElementById("filters").hidden = wantDiscover;
    // a panel opened in one tab must not survive into the other
    document.getElementById("stockpanel").innerHTML = "";
    qEl.value = ""; query = "";
    if(wantDiscover){ renderDiscover(); initCharts(); }
    else renderFeed();
    return;
  }
  const open = ev.target.closest("[data-open]");
  if(open && !ev.target.closest("[data-follow]")){
    ev.preventDefault();
    qEl.value = open.dataset.open;
    query = open.dataset.open;
    // renderStock is async; calling it twice to test for a thenable ran the
    // whole render path a second time on every click.
    Promise.resolve(renderStock(open.dataset.prefer))
      .then(() => { render(); initCharts(); });
    document.getElementById("stockpanel").scrollIntoView({behavior:"smooth", block:"start"});
    return;
  }
  const b = ev.target.closest("button[id^='more-']");
  if(b) expandFund(b.dataset.cik, b);
});

document.getElementById("filters").addEventListener("click", ev => {
  const b = ev.target.closest("button"); if(!b) return;
  if(b.id === "toggle-comp"){
    showComp = !showComp;
    b.setAttribute("aria-pressed", showComp);
  } else {
    filter = b.dataset.f;
    document.querySelectorAll("#filters .chip[data-f]").forEach(c =>
      c.setAttribute("aria-pressed", c === b));
    applyFilterView();
  }
  render();
});

let tip = document.getElementById("tip");
if(!tip){                       // defensive: build the node if markup drifted
  tip = document.createElement("div");
  tip.id = "tip"; tip.setAttribute("role","tooltip"); tip.setAttribute("aria-hidden","true");
  document.body.appendChild(tip);
}
function showTip(el){
  const html = el.getAttribute("data-tip");
  if(!html) return;
  tip.innerHTML = html;
  tip.classList.add("on");
  tip.setAttribute("aria-hidden", "false");
  const r = el.getBoundingClientRect(), t = tip.getBoundingClientRect();
  let x = r.left, y = r.bottom + 8;
  if(x + t.width > innerWidth - 12) x = innerWidth - t.width - 12;
  if(y + t.height > innerHeight - 12) y = r.top - t.height - 8;
  tip.style.left = Math.max(12, x) + "px";
  tip.style.top = Math.max(12, y) + "px";
}
function hideTip(){ tip.classList.remove("on"); tip.setAttribute("aria-hidden","true"); }
document.addEventListener("mouseover", e => {
  const el = e.target.closest("[data-tip]");
  if(el) showTip(el); else if(!tip.contains(e.target)) hideTip();
});
document.addEventListener("focusin", e => {
  const el = e.target.closest("[data-tip]");
  if(el) showTip(el);
});
document.addEventListener("focusout", hideTip);
addEventListener("scroll", hideTip, true);

// ---------- theme ----------
let _stars = null;                 // declared before applyTheme() can call startStars
const THEME_KEY = "virgil.theme";
function applyTheme(t){
  const root = document.documentElement;
  if(t === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", t);
  try { localStorage.setItem(THEME_KEY, t); } catch(e){}
  startStars();
}
function currentTheme(){
  try { return localStorage.getItem(THEME_KEY) || "auto"; } catch(e){ return "auto"; }
}
function isDark(){
  const t = document.documentElement.getAttribute("data-theme");
  if(t) return t === "dark";
  return matchMedia("(prefers-color-scheme: dark)").matches;
}
applyTheme(currentTheme());
document.getElementById("theme").addEventListener("click", () => {
  const order = ["auto", "light", "dark"];
  applyTheme(order[(order.indexOf(currentTheme()) + 1) % order.length]);
});

// ---------- ambient starfield: dark only, and never for reduced motion ----------
function startStars(){
  const cv = document.getElementById("stars");
  if(!cv) return;
  if(_stars){ cancelAnimationFrame(_stars); _stars = null; }
  if(!isDark() || matchMedia("(prefers-reduced-motion: reduce)").matches){
    const c = cv.getContext("2d");
    c && c.clearRect(0, 0, cv.width, cv.height);
    return;
  }
  const ctx = cv.getContext("2d");
  const dpr = Math.min(devicePixelRatio || 1, 2);
  function size(){
    cv.width = innerWidth * dpr; cv.height = innerHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  size();
  addEventListener("resize", size, {passive: true});
  // denser toward the lower half, like dust settling — Rho's field reads that way
  const N = Math.round(Math.min(220, innerWidth * innerHeight / 9000));
  const pts = Array.from({length: N}, () => ({
    x: Math.random() * innerWidth,
    y: Math.pow(Math.random(), 0.65) * innerHeight,
    r: Math.random() * 1.1 + 0.35,
    a: Math.random() * 0.45 + 0.12,
    vy: -(Math.random() * 0.06 + 0.015),
    tw: Math.random() * Math.PI * 2,
  }));
  function frame(){
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    for(const p of pts){
      p.y += p.vy; p.tw += 0.01;
      if(p.y < -4){ p.y = innerHeight + 4; p.x = Math.random() * innerWidth; }
      const a = p.a * (0.72 + 0.28 * Math.sin(p.tw));
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, 6.2832);
      ctx.fillStyle = `rgba(233,238,235,${a.toFixed(3)})`;
      ctx.fill();
    }
    _stars = requestAnimationFrame(frame);
  }
  frame();
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", startStars);


const qEl = document.getElementById("q");
let qt;
qEl.addEventListener("input", () => {
  clearTimeout(qt);
  qt = setTimeout(async () => { query = qEl.value; await renderStock(); render(); initCharts(); }, 120);
});

addEventListener("keydown", e => {
  if(e.key === "/" && document.activeElement !== qEl){ e.preventDefault(); qEl.focus(); }
  if(e.key === "Escape" && document.activeElement === qEl){ qEl.value=""; query=""; renderStock(); render(); }
  if((e.key === "r" || e.key === "R") && document.activeElement !== qEl) location.reload();
});

const V = META.vintage || {};
// The provenance line was a wall of dates across the masthead. It belongs on
// the things it describes, so it is attached as tooltips instead.
function attachProvenance(){
  const map = {
    "activist": `Schedule 13D/G. Latest filings through ${V.feed_latest || "—"}.`,
    "insider":  `Form 4. Filings through ${V.feed_latest || "—"}; median 2 days after the trade.`,
    "buy":      `Form 4 open-market purchases, through ${V.feed_latest || "—"}.`,
    "sell":     `Form 4 open-market sales, through ${V.feed_latest || "—"}.`,
    "fund_trade": `Form 4 filed by a >10% owner, through ${V.feed_latest || "—"}.`,
    "presale":  `Form 144 notices of intent to sell, through ${V.feed_latest || "—"}.`,
  };
  document.querySelectorAll("#filters .chip[data-f]").forEach(c => {
    const extra = map[c.dataset.f];
    if(extra) c.setAttribute("data-tip",
      (LEGEND[c.dataset.f] || "") + "<div class=\"warn\">" + extra + "</div>");
  });
  const stamp = document.getElementById("stamp");
  if(stamp) stamp.setAttribute("data-tip",
    `<div class="t">Where this data comes from</div><dl>` +
    `<dt>13F holdings</dt><dd>quarter ended ${V.f13_period || "—"}</dd>` +
    `<dt>newest filed</dt><dd>${V.f13_filed_latest || "—"}</dd>` +
    `<dt>funds current</dt><dd>${V.f13_current || 0} of ${V.f13_funds || 0}</dd>` +
    `<dt>insider &amp; activist</dt><dd>through ${V.feed_latest || "—"}</dd>` +
    `<dt>congress</dt><dd>${V.congress_members || 0} members, to ${V.congress_latest || "—"}</dd>` +
    `</dl><div class="warn">A 13F reports holdings on the quarter-end date only —
      a change happened somewhere inside that window, never on the filing date.</div>`);
}
function isoish(d){ return d && d.length === 8 ? isoDay(d) : (d || "—"); }
attachProvenance();
const _unusedVintage = [
  V.f13_period ? `<span>13F holdings — quarter ended <b>${V.f13_period}</b>,
     newest filed <b>${V.f13_filed_latest}</b> · <b>${V.f13_current}</b> of ${V.f13_funds}
     funds current${(V.f13_stale||[]).length ? `, ${V.f13_stale.length} stale` : ""}</span>` : "",
  V.feed_latest ? `<span>Insider &amp; activist filings through <b>${V.feed_latest}</b></span>` : "",
  V.clusters_latest ? `<span>Cluster scan through <b>${isoish(V.clusters_latest)}</b></span>` : "",
  V.congress_latest ? `<span>Congress — ${V.congress_members} members, trades through
     <b>${V.congress_latest}</b></span>` : "",
].filter(Boolean).join("");

document.getElementById("stamp").textContent =
  META.generated ? "updated " + new Date(META.generated).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}) : "—";
document.getElementById("interval").textContent = META.interval || "5 min";

paintFollowCount();
render(); renderStock().then ? renderStock().then(initCharts) : initCharts(); renderStakes(); renderClusters(); renderPeople(); renderFirms();
// Live updates without reloading.
//
// The page used to call location.reload() on a timer: 2.9 MB re-fetched, a
// visible flash, scroll position and any open panel lost — and it only ever
// showed something new if a deploy had happened in between, because the events
// were baked into the HTML.
//
// Now the embedded copy is just the first paint, so the page is readable
// immediately, and a small marker file is polled for changes. Only when the
// build stamp moves does the full payload get fetched and swapped in place.
const LIVE_POLL_MS = 30000;
let liveStamp = META.generated || "";
let liveFails = 0;

async function checkForUpdate(){
  try{
    const base = DATA_BASE || "";
    const r = await fetch(`${base}data/live-meta.json?t=${Date.now()}`, {cache: "no-store"});
    if(!r.ok) throw new Error(String(r.status));
    const meta = await r.json();
    liveFails = 0;
    if(!meta.built || meta.built === liveStamp) return;

    const full = await fetch(`${base}data/live.json?t=${Date.now()}`, {cache: "no-store"});
    if(!full.ok) throw new Error(String(full.status));
    const d = await full.json();
    if(Array.isArray(d.events)) EVENTS = d.events;
    if(Array.isArray(d.clusters)) CLUSTERS = d.clusters;
    if(d.quotes && typeof d.quotes === "object") QUOTES = d.quotes;
    liveStamp = meta.built;

    // Re-render whichever view is showing, leaving scroll and any open panel
    // alone. A filter the reader is typing into is not interrupted.
    const wantDiscover = document.getElementById("tab-discover")
      && document.getElementById("tab-discover").classList.contains("on");
    if(wantDiscover) renderDiscover(); else render();
    const stamp = document.getElementById("stamp");
    if(stamp) stamp.textContent = "updated " + new Date(meta.built)
      .toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
  }catch(e){
    // A deploy can change the page's own code, at which point swapping data
    // into the old one is not safe. Reload after repeated failures instead.
    if(++liveFails >= 10){ liveFails = 0; if(!query) location.reload(); }
  }
}
setInterval(checkForUpdate, LIVE_POLL_MS);
document.addEventListener("visibilitychange", () => {
  if(document.visibilityState === "visible") checkForUpdate();
});
</script>
</body>
</html>
"""


def build(interval_label="60s", reload_seconds=60, data_only=False,
          live_only=False):
    events = load("events.json", [])
    # 13D/G rows carry an issuer CIK but no ticker, so they could not be
    # expanded like every other row. Map CIK -> ticker from SEC's own file.
    cik_tkr = {}
    try:
        import urllib.request as _u
        _rq = _u.Request("https://www.sec.gov/files/company_tickers.json",
                         headers={"User-Agent": "sean@erised.me virgil/0.1"})
        for v in json.loads(_u.urlopen(_rq, timeout=30).read()).values():
            cik_tkr.setdefault(str(v["cik_str"]).zfill(10), v["ticker"])
    except Exception:
        pass
    for e in events:
        if not e.get("ticker") and e.get("issuer_cik"):
            t = cik_tkr.get(str(e["issuer_cik"]).zfill(10))
            if t:
                e["ticker"] = t

    principals = {v["person"] for v in load("funds.json", {}).values()}
    for e in events:
        # EDGAR headers carry HTML entities ("Soho House &amp; Co")
        e["issuer"] = _html.unescape(e.get("issuer") or "")
        e["actor"] = _html.unescape(e.get("actor") or "")
    events = aggregate(normalize_actors(events, principals))
    people = load("people.json", {})
    firms = load("ciks.json", {"firms": {}})["firms"]
    clusters = load("clusters.json", [])
    # Citadel alone reports 7,698 positions; inlining every fund's full book
    # would be ~6.7 MB. Top 60 by value covers what anyone actually reads, and
    # concentrated books (Berkshire has 30) come through whole.
    TOP_N = 60
    holdings = {}
    hdir = os.path.join(DATA, "holdings")
    if os.path.isdir(hdir):
        for fn in sorted(os.listdir(hdir)):
            if not fn.endswith(".json"):
                continue
            d = json.load(open(os.path.join(hdir, fn)))
            rows = sorted(d["positions"].values(), key=lambda p: -(p["value"] or 0))
            for r in rows:
                r["issuer"] = _html.unescape(r.get("issuer") or "")
            tot = sum(p["value"] or 0 for p in d["positions"].values())
            acts = {}
            for p_ in d["positions"].values():
                acts[p_["action"]] = acts.get(p_["action"], 0) + 1
            cmap = load("cusip_map.json", {})
            for r, (cu, _) in zip(rows, sorted(d["positions"].items(),
                                               key=lambda kv: -(kv[1]["value"] or 0))):
                r["cusip"] = cu
                r["ticker"] = (cmap.get(cu) or {}).get("ticker")
            holdings[d["fund"]] = {
                "fund": d["fund"], "person": d["person"], "cik": d["cik"],
                "period": d["current"], "previous": d["previous"],
                "filed": d["filed"], "total_positions": len(d["positions"]),
                "total_value": tot, "actions": acts, "rows": rows[:TOP_N],
            }

    # price range over the quarter a position changed in - the exact trade date
    # is never disclosed, so a range is the most precise honest answer
    pxdir = os.path.join(DATA, "prices")
    px_cache = {}
    if os.path.isdir(pxdir):
        for fn in os.listdir(pxdir):
            if fn.endswith(".json"):
                try:
                    px_cache[fn[:-5]] = json.load(open(os.path.join(pxdir, fn)))
                except Exception:
                    pass

    def quarter_range(ticker, start, end):
        d = px_cache.get((ticker or "").upper())
        if not d or not d.get("series"):
            return None
        vals = [c for dt, c in d["series"] if start < dt <= end]
        if not vals:
            return None
        return {"lo": round(min(vals), 2), "hi": round(max(vals), 2),
                "avg": round(sum(vals) / len(vals), 2),
                "now": d.get("price") or d["series"][-1][1], "days": len(vals)}

    for h in holdings.values():
        prev = h.get("previous")
        start = prev or "0000-00-00"
        for r in h["rows"]:
            r["window"] = quarter_range(r.get("ticker"), start, h["period"])

    fund_history = load("fund_history.json", {})
    quotes = (load("quotes.json", {}) or {}).get("quotes", {})
    quote_meta = load("quotes.json", {}) or {}

    if live_only:
        # Everything below loads the 44 MB of holdings and rewrites every
        # per-ticker and per-fund block — none of which changes when a filing
        # lands. The three inputs readers actually poll are ready here.
        side = os.path.join(ROOT, "ui", "data")
        os.makedirs(side, exist_ok=True)
        built = datetime.now(timezone.utc).isoformat(timespec="seconds")
        json.dump({"built": built, "events": events, "clusters": clusters,
                   "quotes": quotes},
                  open(os.path.join(side, "live.json"), "w"), separators=(",", ":"))
        json.dump({"built": built, "events": len(events)},
                  open(os.path.join(side, "live-meta.json"), "w"),
                  separators=(",", ":"))
        return None, {"events": len(events), "hash": "live"}
    firm_logos = load("firm_logos.json", {})
    fund_logos = load("fund_logos.json", {})
    # A partnership entry (Optiver, Jane Street) has no principal to photograph,
    # so hand the page its company mark alongside the person record.
    for _p in people.values():
        if _p.get("is_firm"):
            _p["firm_logo"] = (fund_logos.get(_p.get("firm")) or {}).get("logo_local")
    congress = load(os.path.join("congress", "trades.json"), [])
    cong_by_ticker = load(os.path.join("congress", "by_ticker.json"), {})
    members = {}
    for e in congress:
        m = members.setdefault(e["member"], {"member": e["member"], "state": e.get("state"),
                                             "trades": [], "buys": 0, "sells": 0})
        m["trades"].append(e)
        if e["action"] == "buy":
            m["buys"] += 1
        elif e["action"] == "sell":
            m["sells"] += 1
    for m in members.values():
        m["trades"] = m["trades"][:40]
        m["last"] = m["trades"][0]["txn_iso"] if m["trades"] else None

    insiders_tkr = load("insiders_by_ticker.json", {})
    for _t, _rows in insiders_tkr.items():
        seen_rows, deduped = set(), []
        for r in _rows:
            r["who"] = person_name(_html.unescape(r.get("who") or ""))
            r["issuer"] = _html.unescape(r.get("issuer") or "")
            f = r.get("filed") or ""
            if len(f) == 8 and f.isdigit():
                r["filed"] = f"{f[:4]}-{f[4:6]}-{f[6:]}"
            key = (r["who"], r.get("date"), r.get("shares"), r.get("price"), r["action"])
            if key in seen_rows:
                continue
            seen_rows.add(key)
            deduped.append(r)
        insiders_tkr[_t] = deduped
    cmap_all = load("cusip_map.json", {})
    ticker_index = {}
    hdir2 = os.path.join(DATA, "holdings")
    if os.path.isdir(hdir2):
        for fn in sorted(os.listdir(hdir2)):
            if not fn.endswith(".json"):
                continue
            d = json.load(open(os.path.join(hdir2, fn)))
            meta = holdings.get(d["fund"], {})
            total = len(d["positions"])
            ranked = sorted(d["positions"].items(), key=lambda kv: -(kv[1]["value"] or 0))
            for rank, (cu, r) in enumerate(ranked, 1):
                t = ((cmap_all.get(cu) or {}).get("ticker") or "").upper()
                if not t:
                    continue
                e = ticker_index.setdefault(t, {"ticker": t,
                                                "name": _html.unescape(r.get("issuer") or ""),
                                                "funds": [], "congress": []})
                e["funds"].append({
                    "fund": d["fund"], "person": d["person"],
                    "period": d["current"], "previous": d["previous"],
                    "filed": d["filed"], "action": r["action"], "shares": r["shares"],
                    "prev_shares": r.get("prev_shares"), "value": r["value"],
                    "delta_pct": r.get("delta_pct"), "delta_shares": r.get("delta_shares"),
                    "stale": meta.get("stale"), "rank": rank, "of": total,
                })
    for t, lst in insiders_tkr.items():
        ticker_index.setdefault(t.upper(), {"ticker": t.upper(),
                                            "name": (lst[0].get("issuer") or "")[:60],
                                            "funds": [], "congress": []})
    for t, lst in cong_by_ticker.items():
        e = ticker_index.setdefault(t.upper(), {"ticker": t.upper(),
                                                "name": (lst[0].get("asset") or "")[:60],
                                                "funds": [], "congress": []})
        e["congress"] = lst[:12]
    for t, e in ticker_index.items():
        e["funds"].sort(key=lambda f: -(f["value"] or 0))
        e["holders"] = len(e["funds"])
        e["funds"] = e["funds"][:20]
        px = px_cache.get(t)
        if px and px.get("series"):
            e["series"] = px["series"][-90:]
            e["close"] = px["series"][-1][1]
            e["close_date"] = px["series"][-1][0]

    stocks = {}
    sdir = os.path.join(DATA, "stocks")
    if os.path.isdir(sdir):
        for fn in sorted(os.listdir(sdir)):
            if fn.endswith(".json"):
                d = json.load(open(os.path.join(sdir, fn)))
                for t in d.get("trades", []):
                    t["who"] = person_name(_html.unescape(t.get("who") or ""))
                for pz in d.get("presales", []):
                    w = _html.unescape(pz.get("who") or "")
                    pz["who"] = w.title() if w.isupper() else w
                stocks[d["ticker"]] = d
    for c in clusters:
        for b in c.get("buyers", []):
            b["name"] = person_name(_html.unescape(b.get("name") or ""))
        c["issuer"] = _html.unescape(c.get("issuer") or "")
    latest_period = max((h["period"] for h in holdings.values() if h.get("period")),
                        default=None)
    for h in holdings.values():
        h["stale"] = bool(latest_period and h.get("period") and h["period"] < latest_period)
    stale_funds = sorted(h["fund"] for h in holdings.values() if h["stale"])

    periods = sorted({h["period"] for h in holdings.values() if h.get("period")})
    hfiled = sorted({h["filed"] for h in holdings.values() if h.get("filed")})
    vintage = {
        "f13_period": periods[-1] if periods else None,
        "f13_periods": periods,
        "f13_filed_latest": hfiled[-1] if hfiled else None,
        "f13_funds": len(holdings),
        "f13_current": sum(1 for h in holdings.values() if not h["stale"]),
        "f13_stale": stale_funds,
        "feed_latest": max((e["filed"] for e in events), default=None),
        "clusters_latest": max((c.get("last_filed") or "" for c in clusters), default=None),
        "congress_latest": max((e["txn_iso"] for e in congress), default=None),
        "congress_members": len(members),
    }
    meta = {"vintage": vintage,
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "interval": interval_label, "reload_seconds": reload_seconds,
            "events": len(events), "clusters": len(clusters),
            "stocks": sorted(stocks), "funds": sorted(holdings),
            "members": len(members), "congress_trades": len(congress),
            "congress_latest": max((e["txn_iso"] for e in congress), default=None)}

    side = os.path.join(ROOT, "ui", "data")
    os.makedirs(side, exist_ok=True)
    slim_funds = {}
    for name, h in holdings.items():
        full = json.load(open(os.path.join(DATA, "holdings", f"{h['cik']}.json")))
        rank = sorted(full["positions"].items(), key=lambda kv: -(kv[1]["value"] or 0))
        cm = load("cusip_map.json", {})
        rows = []
        for cu, r in rank:
            rows.append({**r, "cusip": cu,
                         "ticker": (cm.get(cu) or {}).get("ticker"),
                         "issuer": _html.unescape(r.get("issuer") or "")})
        for r in rows:
            r["window"] = quarter_range(r.get("ticker"), h.get("previous") or "0000-00-00",
                                        h["period"])
        json.dump({**{k: v for k, v in h.items() if k != "rows"}, "rows": rows},
                  open(os.path.join(side, f"fund-{h['cik']}.json"), "w"),
                  separators=(",", ":"))
        fl = fund_logos.get(name) or {}
        h["logo"] = fl.get("logo")
        h["logo_local"] = fl.get("logo_local")
        h["logo_chip"] = fl.get("chip")
        h["logo_sharp"] = bool(fl.get("sharp"))
        h["logo_wide"] = bool(fl.get("wide"))
        hist = (fund_history.get(name) or {}).get("series") or []
        h["history"] = hist
        slim_funds[name] = {k: v for k, v in h.items() if k != "rows"}
        slim_funds[name]["rows"] = h["rows"][:25]
    ACTIONS = ["held", "added", "trimmed", "new position", "exited"]
    fund_tbl, fund_ix = [], {}
    def fund_ref(h):
        key = (h["fund"], h["person"], h["period"], h.get("previous"),
               h["filed"], bool(h.get("stale")))
        if key not in fund_ix:
            fund_ix[key] = len(fund_tbl)
            fund_tbl.append(list(key))
        return fund_ix[key]

    compact = {}
    for t, e in ticker_index.items():
        rows = []
        for h in e["funds"]:
            rows.append([fund_ref(h),
                         ACTIONS.index(h["action"]) if h["action"] in ACTIONS else 0,
                         round(h["shares"] or 0), round(h.get("prev_shares") or 0),
                         round(h["value"] or 0),
                         None if h.get("delta_pct") is None else round(h["delta_pct"], 1),
                         h.get("rank"), h.get("of")])
        c = {"n": e["name"], "h": e["holders"], "f": rows}
        if e.get("congress"):
            c["c"] = e["congress"]
        ins = insiders_tkr.get(t)
        if ins:
            c["i"] = ins[:24]
        if e.get("series"):
            c["s"] = e["series"]
            c["px"] = e.get("close")
            c["pd"] = e.get("close_date")
        compact[t] = c
    # One 6.8 MB file meant every first search downloaded the whole market and
    # built 86k objects before rendering. Shard by first character: a lookup
    # pulls ~150 KB and the shared fund table once.
    tdir = os.path.join(side, "tickers")
    os.makedirs(tdir, exist_ok=True)
    for stale_f in os.listdir(tdir):
        os.remove(os.path.join(tdir, stale_f))
    shards = {}
    for t, c in compact.items():
        key = t[0].upper() if t and t[0].isalnum() else "_"
        shards.setdefault(key, {})[t] = c
    for key, block in shards.items():
        json.dump(block, open(os.path.join(tdir, f"{key}.json"), "w"),
                  separators=(",", ":"))
    json.dump({"actions": ACTIONS, "funds": fund_tbl,
               "shards": sorted(shards), "count": len(compact)},
              open(os.path.join(tdir, "_index.json"), "w"), separators=(",", ":"))
    # Live payload: the page polls the tiny marker and only pulls the full file
    # when the build stamp moves, so a reader sees new filings without the page
    # reloading and without waiting on a deploy.
    live = {"built": meta.get("generated"),
            "events": events, "clusters": clusters, "quotes": quotes}
    json.dump(live, open(os.path.join(side, "live.json"), "w"),
              separators=(",", ":"))
    json.dump({"built": live["built"], "events": len(events)},
              open(os.path.join(side, "live-meta.json"), "w"),
              separators=(",", ":"))

    # Investor-filer index for the "add this person" lookup, sharded the same
    # way as tickers: a search touches one ~80 KB block, not 1.9 MB.
    # The chart fetches data/prices/<TICKER>.json relative to the page. In
    # production that resolves to the bucket; locally the server's root is ui/,
    # so link the price files in rather than copying 39 MB of duplicates.
    _px = os.path.join(DATA, "prices")
    _link = os.path.join(side, "prices")
    if os.path.isdir(_px) and not os.path.exists(_link):
        try:
            os.symlink(_px, _link)
        except OSError:
            pass

    # the page fetches this at search time to map a person to their fund
    _al = load("aliases.json", {})
    if _al:
        json.dump(_al, open(os.path.join(side, "aliases.json"), "w"),
                  separators=(",", ":"))

    filers = load("filers.json", {})
    fdir = os.path.join(side, "filers")
    os.makedirs(fdir, exist_ok=True)
    for stale_f in os.listdir(fdir):
        os.remove(os.path.join(fdir, stale_f))
    fshards = {}
    for cik, rec in filers.items():
        nm = (rec.get("n") or "").strip()
        if not nm:
            continue
        for tok in re.split(r"[^A-Za-z0-9]+", nm)[:6]:
            if not tok:
                continue
            key = tok[0].upper() if tok[0].isalnum() else "_"
            fshards.setdefault(key, {})[cik] = rec
    for key, block in fshards.items():
        json.dump(block, open(os.path.join(fdir, f"{key}.json"), "w"),
                  separators=(",", ":"))
    print(f"  filer index: {len(filers)} entities in {len(fshards)} shards")

    old = os.path.join(side, "tickers.json")
    if os.path.exists(old):
        os.remove(old)

    html = (TEMPLATE
            .replace("/*__EVENTS__*/[]", json.dumps(events, separators=(",", ":")))
            .replace("/*__PEOPLE__*/{}", json.dumps(people, separators=(",", ":")))
            .replace("/*__FIRMS__*/{}", json.dumps(firms, separators=(",", ":")))
            .replace("/*__CLUSTERS__*/[]", json.dumps(clusters, separators=(",", ":")))
            .replace("/*__STOCKS__*/{}", json.dumps(stocks, separators=(",", ":")))
            .replace("/*__HOLDINGS__*/{}", json.dumps(slim_funds, separators=(",", ":")))
            .replace("/*__MEMBERS__*/{}", json.dumps(members, separators=(",", ":")))
            .replace("/*__QUOTES__*/{}", json.dumps(quotes, separators=(",", ":")))
            .replace("/*__QMETA__*/{}", json.dumps(
                {k: v for k, v in quote_meta.items() if k != "quotes"}, separators=(",", ":")))
            .replace("/*__LOGOS__*/{}", json.dumps(firm_logos, separators=(",", ":")))
            .replace("/*__TRACKED__*/{}", json.dumps(
                {str(v.get("cik", "")).zfill(10): k
                 for k, v in load("funds.json", {}).items() if v.get("cik")},
                separators=(",", ":")))
            .replace("/*__CONGTKR__*/{}", "{}")
            .replace("/*__TICKERS__*/[]", json.dumps(
                sorted(ticker_index), separators=(",", ":")))
            .replace("/*__META__*/{}", json.dumps(meta, separators=(",", ":"))))

    # Hash the payload, not the page: the page embeds a timestamp that changes
    # every cycle even when nothing was filed, and re-uploading that is the
    # single largest line item in the bill.
    # `compact` belongs in here: a market-wide insider trade changes only the
    # per-ticker shards, and leaving it out meant those edits built locally and
    # were never uploaded, so search results stayed a day behind the feed.
    payload = json.dumps([events, clusters, stocks, people, firms, compact,
                          fund_logos],
                         sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(payload).hexdigest()
    open(os.path.join(DATA, ".content_hash"), "w").write(digest)
    meta["hash"] = digest[:12]

    # Guard against the failure that shipped a broken tooltip: a .replace()
    # whose anchor had drifted silently produced a page missing #tip, so the
    # hover handler threw on the first mouseover.
    missing = [m for m in ('id="tip"', 'id="stockpanel"', 'id="q"') if m not in html]
    leftover = re.findall(r"/\*__[A-Z_]+__\*/", html)
    missing += [f"unsubstituted {t}" for t in sorted(set(leftover))]
    if missing:
        raise SystemExit("build aborted, page is missing: " + ", ".join(missing))

    import shutil, subprocess, tempfile
    node = shutil.which("node")
    if node:
        blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
        for i, b in enumerate(blocks):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
                fh.write(b)
                tmp = fh.name
            r = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
            os.unlink(tmp)
            if r.returncode:
                raise SystemExit("build aborted, script block %d has a syntax error:\n%s"
                                 % (i, r.stderr[:600]))

    out = os.path.join(ROOT, "ui", "index.html")
    if data_only:
        # The frequent cycle only refreshes what readers fetch from storage —
        # the page's own code is unchanged, so rewriting and republishing it
        # every few minutes buys nothing and costs a render check, a commit and
        # a deploy each time.
        return None, meta
    open(out, "w").write(html)
    return out, meta


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-only", action="store_true",
                    help="refresh data/ for the live page; leave index.html alone")
    ap.add_argument("--live-only", action="store_true",
                    help="only the blocks readers poll; skip the daily shards")
    a = ap.parse_args()
    out, meta = build(data_only=a.data_only or a.live_only, live_only=a.live_only)
    if out:
        print(f"built {out}  ({os.path.getsize(out)/1024:.0f} KB, "
              f"{meta['events']} events, hash {meta['hash']})")
    else:
        print(f"data refreshed ({meta['events']} events, hash {meta['hash']}) "
              f"- page untouched")
