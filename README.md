# Virgil

Fast-signal SEC filing tracker for notable investors.

The premise is that the *speed* of a filing matters more than its completeness.
A 13F tells you what a fund held at a quarter end, 45 days after the fact. A
Form 4 tells you what an insider actually traded, with the exact date and
execution price, a median of two days later. Measured across this dataset:

| Signal | Form | Typical lag |
|---|---|---|
| Activist stake change | SC 13D/A | 0 days |
| Insider trade | Form 4 | 2 days |
| Pre-sale notice | Form 144 | leads the trade |
| Congressional trade | STOCK Act PTR | 27 days |
| Fund holdings | 13F-HR | 45+ days |

## Layout

    ingest/     EDGAR clients, parsers and enrichment
    ui/         page generator, local server, smoke test
    run.sh      one poll cycle: fetch -> build -> verify -> publish

`run.sh` is installed as a launchd job that runs every 60 seconds during EDGAR
hours. It refuses to publish if the rendered page comes back empty, and skips
the upload entirely when the content hash is unchanged.

## Setup

    pip3 install pillow opencv-python cairosvg
    mkdir -p ~/.virgil && chmod 700 ~/.virgil
    printf 'DATABENTO_API_KEY=...\n' > ~/.virgil/credentials
    chmod 600 ~/.virgil/credentials

    python3 ui/serve.py 8770     # http://localhost:8770

Credentials are read from `~/.virgil/credentials`, deliberately outside this
tree so they cannot be committed.

## Data

Everything comes from public sources: SEC EDGAR (filings), the House Clerk
(congressional PTRs), Yahoo (quotes and split-adjusted closes), and Databento
(licensed daily bars). Logos come from LinkedIn, Wikidata and firms' own sites;
portraits from Wikipedia and verified image search.
