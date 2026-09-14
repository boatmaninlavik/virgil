#!/bin/zsh
# One poll cycle: fetch new filings -> rebuild page -> push to GCS.
# Installed as a launchd job; safe to run by hand.
set -euo pipefail
cd "$(dirname "$0")"

BUCKET="gs://virgil-edgar"
WEB_BUCKET="gs://virgil-web"   # public, CORS-locked; serves the page's data
PY="${PY:-python3}"
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

# EDGAR accepts filings 06:00-22:00 ET on weekdays. Outside that window nothing
# new can land, so skip the poll instead of hammering sec.gov overnight.
eth=$(TZ=America/New_York date +%H)
etd=$(TZ=America/New_York date +%u)
FORCE="${FORCE:-0}"
if [[ "$FORCE" != "1" && ( $etd -gt 5 || $eth -lt 6 || $eth -ge 22 ) ]]; then
  echo "$(date -u +%FT%TZ) outside EDGAR hours (ET $eth:00, day $etd) - skipping poll"
  exit 0
fi

echo "$(date -u +%FT%TZ) poll start"

# Investors the user asked for from the page land in data/requests.json; pick
# them up before polling so their filings are included in this same cycle.
if [[ -s data/requests.json ]]; then
  $PY ingest/add_entity.py 2>&1 | tail -3 || echo "add-entity skipped"
fi

$PY ingest/poll.py --limit 6 2>&1 | tail -3

# Portraits and logos for anyone still missing one — including investors the
# user added, whose photo could not be fetched at the time. Bounded per run:
# the fetcher is serial and memory-guarded, and commits each result, so a small
# daily slice fills the gaps without ever loading the machine.
if [[ "$eth" == "06" ]]; then
  $PY ingest/photos.py >/dev/null 2>&1 || echo "wiki photo refresh failed (non-fatal)"
  $PY ingest/headshots.py --limit 12 >> data/market.log 2>&1 || \
    echo "headshot refresh failed (non-fatal)"
  $PY ingest/fill_logos.py >> data/market.log 2>&1 || \
    echo "logo refresh failed (non-fatal)"
fi

# Market-wide cluster scan is ~1,200-2,000 filings per trading day, so it runs
# once daily rather than every cycle. 07:xx ET picks up the prior session,
# whose daily index is published after the close.
if [[ "$eth" == "07" && ! -f "data/.market-$(date +%F)" ]]; then
  touch "data/.market-$(date +%F)"
  rm -f data/.market-* 2>/dev/null; touch "data/.market-$(date +%F)"
  $PY ingest/market.py --days 3 >> data/market.log 2>&1 || echo "market scan failed (non-fatal)"
  # Was never scheduled — the per-stock index was only ever built by hand and
  # had drifted two days stale. It backfills whatever the live lane missed.
  $PY ingest/insiders_by_ticker.py --days 4 >> data/market.log 2>&1 || \
    echo "ticker index refresh failed (non-fatal)"
  # 13F is quarterly; a daily refresh is already far more often than it changes
  $PY ingest/holdings.py >> data/market.log 2>&1 || echo "13F refresh failed (non-fatal)"
  # House PTRs: new filings appear daily; parsed PDFs are cached by doc id
  $PY ingest/congress.py --years "$(date +%Y)" >> data/market.log 2>&1 || \
    echo "congress refresh failed (non-fatal)"
fi

# Incremental price update once per day. Metered API, so it is ceiling-guarded
# and only ever buys bars it does not already hold ($0.0019 a day, not $0.93).
if [[ $((10#$(date +%M))) -lt 2 && "$eth" == "07" ]]; then
  $PY ingest/daily_prices.py >> data/market.log 2>&1 || echo "price update skipped"
  # Databento bars are raw prints, so a split draws a cliff that never happened
  # - CVNA showed an 80% overnight crash. Re-check against adjusted closes.
  $PY ingest/splits.py >> data/market.log 2>&1 || echo "split check skipped"
fi

# Per-ticker rescoring refresh hourly (prices.py caches for 6h); re-scoring the indexed
# tickers is cheap and keeps the "since those trades" numbers honest.
if [[ -d data/stocks && $((10#$(date +%M))) -lt 2 ]]; then
  for f in data/stocks/*.json; do
    [[ -e "$f" ]] || continue
    tk=$(basename "$f" .json)
    $PY ingest/stock.py "$tk" --limit 45 >/dev/null 2>&1 || true
  done
fi

# Market-wide insider trades, every cycle. The daily index only publishes after
# the close, so without this an insider sale on a stock we index but do not
# follow could not surface until the next morning. EDGAR's getcurrent feed is
# live to the minute; the daily scan below still runs and reconciles.
$PY ingest/live_f4.py 2>&1 | tail -1 || echo "live lane skipped"

# Live quotes are free and keyless, so they refresh every cycle during market
# hours; the metered Databento call stays on its once-a-day schedule.
if [[ $((10#$(date +%M))) -lt 3 ]]; then
  $PY ingest/live_quotes.py >> data/market.log 2>&1 || echo "quotes skipped"
fi

$PY ui/build.py

# Headless Chrome leaves a profile cache behind on every run. Left alone it
# reached 4.2 GB and filled the disk mid-build; prune it above 200 MB.
CHCACHE="$HOME/Library/Caches/Google/Chrome-headless"
if [[ -d "$CHCACHE" ]]; then
  sz=$(du -sm "$CHCACHE" 2>/dev/null | cut -f1)
  if [[ -n "$sz" && "$sz" -gt 200 ]]; then
    rm -rf "$CHCACHE"
    echo "$(date -u +%FT%TZ) pruned ${sz}MB of headless Chrome cache"
  fi
fi

# A blank page has shipped three times from a token that parsed but threw at
# runtime. Render it headlessly and refuse to publish if it comes back empty.
if [[ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]] && \
   curl -sf -o /dev/null -m 3 http://localhost:8770/ 2>/dev/null; then
  if ! $PY ui/smoke.py; then
    echo "$(date -u +%FT%TZ) smoke test failed - not publishing"
    exit 1
  fi
fi

# Skip the upload when nothing was filed. Most cycles change nothing, and
# Class A operations - not bytes stored - dominate the GCS bill.
NEW_HASH=$(cat data/.content_hash 2>/dev/null || echo none)
OLD_HASH=$(cat data/.synced_hash 2>/dev/null || echo none)
if [[ "$NEW_HASH" == "$OLD_HASH" && "${FORCE_SYNC:-0}" != "1" ]]; then
  echo "$(date -u +%FT%TZ) no change ($NEW_HASH) - skipping upload"
  exit 0
fi

[[ -d ui/data ]] && gcloud storage cp -r -Z ui/data "$BUCKET/" --quiet 2>/dev/null
# Public copy the deployed page reads from: the repo carries index.html only,
# so the sharded blocks have to be reachable over HTTP from the live domain.
[[ -d ui/data ]] && gcloud storage rsync -r -c ui/data "$WEB_BUCKET/data" \
  --quiet 2>/dev/null || true
gcloud storage cp ui/index.html "$BUCKET/index.html" \
  --content-type=text/html \
  --cache-control="no-cache, max-age=0" --quiet
gcloud storage cp data/events.json "$BUCKET/data/events.json" \
  --content-type=application/json --cache-control="no-cache, max-age=0" --quiet
gcloud storage cp data/people.json "$BUCKET/data/people.json" \
  --content-type=application/json --quiet
[[ -f data/clusters.json ]] && gcloud storage cp data/clusters.json \
  "$BUCKET/data/clusters.json" --content-type=application/json \
  --cache-control="no-cache, max-age=0" --quiet
gcloud storage cp data/funds.json  "$BUCKET/data/funds.json"  --content-type=application/json --quiet
gcloud storage cp data/ciks.json   "$BUCKET/data/ciks.json"   --content-type=application/json --quiet

[[ -d data/stocks ]] && gcloud storage cp -r data/stocks "$BUCKET/data/" --quiet 2>/dev/null
[[ -d data/prices ]] && gcloud storage cp -r data/prices "$BUCKET/data/" --quiet 2>/dev/null
[[ -f data/congress/trades.json ]] && gcloud storage cp data/congress/trades.json \
  "$BUCKET/data/congress-trades.json" --content-type=application/json --quiet 2>/dev/null
cp data/.content_hash data/.synced_hash 2>/dev/null
echo "$(date -u +%FT%TZ) synced -> $BUCKET ($NEW_HASH)"

# Push the same build to Vercel when it is configured. Silent no-op otherwise,
# so the local-only setup keeps working exactly as before.
if [[ -d .vercel ]] && command -v vercel >/dev/null 2>&1; then
  if ./deploy.sh >> data/deploy.log 2>&1; then
    echo "$(date -u +%FT%TZ) deployed to vercel"
  else
    echo "$(date -u +%FT%TZ) vercel deploy failed (see data/deploy.log)"
  fi
fi
