"""Fetch logos for any tracked entity still without one.

Runs daily so that an investor added from the page ends up looking like every
other fund without anyone having to ask. LinkedIn first - it has a square mark
for nearly every firm and does not block us - then the firm's own site.

Bounded per run: logo lookups are network-bound and there is no hurry.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linkedin
import site_logos as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def load(name):
    try:
        return json.load(open(os.path.join(DATA, name)))
    except Exception:
        return {}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    a = ap.parse_args()

    fund, firm = load("fund_logos.json"), load("firm_logos.json")
    funds, ciks = load("funds.json"), (load("ciks.json") or {}).get("firms", {})
    todo = [(k, fund) for k, v in fund.items() if not v.get("logo_local")] + \
           [(k, firm) for k, v in firm.items() if not v.get("logo_local")]
    todo = todo[:a.limit]
    if not todo:
        print("every tracked entity has a logo")
        sys.exit(0)

    got = 0
    for key, tgt in todo:
        # The registered name disambiguates: "Ares" finds ARES IT Services on
        # LinkedIn, "ARES MANAGEMENT LLC" finds the right firm.
        full = ((funds.get(key) or {}).get("edgar_name")
                or (ciks.get(key) or {}).get("name") or key)
        raw = None
        for nm in dict.fromkeys([full, key]):
            try:
                raw, _ = linkedin.logo(nm)
            except Exception:
                raw = None
            if raw:
                break
        path = chip = None
        if raw:
            path, chip, _ = S.render(raw, S.slugify(key))
        if not path:
            dom = tgt[key].get("domain")
            if dom:
                try:
                    path, chip = S.best(dom, S.slugify(key), full)
                except Exception:
                    path = None
        if path:
            tgt[key].update({"logo_local": path, "sharp": True, "chip": chip,
                             "source": "linkedin" if raw else "site"})
            got += 1
        print(f"  {key[:28]:30s} {'ok' if path else 'none'}", flush=True)

    json.dump(fund, open(os.path.join(DATA, "fund_logos.json"), "w"), indent=1)
    json.dump(firm, open(os.path.join(DATA, "firm_logos.json"), "w"), indent=1)
    print(f"{got} logos added")
