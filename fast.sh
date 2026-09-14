#!/bin/zsh
# The frequent cycle: fetch what is new, refresh what readers fetch, stop.
#
# Deliberately much smaller than run.sh. The page now reads its data from object
# storage, so a routine cycle has no reason to rebuild index.html, run the
# render check, commit, or wait on a deploy — none of which change for a new
# filing. Dropping them takes a cycle from about five minutes of billable
# runner time to well under one, which is the difference between this costing
# real money every month and costing nothing.
#
# run.sh remains the full pipeline: page, guards, publish, daily scans.
set -euo pipefail
setopt nullglob
cd "$(dirname "$0")"

WEB_BUCKET="${WEB_BUCKET:-gs://virgil-web}"
PY="${PY:-python3}"
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

echo "$(date -u +%FT%TZ) fast cycle"

# Investors queued from the live site, then the three fast lanes.
$PY ingest/add_entity.py 2>&1 | tail -2 || echo "add-entity skipped"
$PY ingest/poll.py --limit 6 2>&1 | tail -2
$PY ingest/live_f4.py 2>&1 | tail -1 || echo "live lane skipped"
# Quotes cover the whole 7,200-symbol universe and take about three minutes,
# which is the entire cost of this cycle — everything else finishes in seconds.
# A price that is a quarter-hour old is fine next to filings that are days old,
# so refresh them on their own cadence rather than on every poll.
QSTAMP=data/.quotes_at
if [[ ! -f "$QSTAMP" ]] || \
   (( $(date +%s) - $(cat "$QSTAMP" 2>/dev/null || echo 0) > 900 )); then
  if $PY ingest/live_quotes.py > /dev/null 2>&1; then
    date +%s > "$QSTAMP"
    echo "quotes refreshed"
  else
    echo "quotes skipped"
  fi
fi

# Adding an investor changes the payload baked into the page, so that cycle has
# to do the whole job: rebuild, verify it renders, commit, deploy.
if [[ -f data/.needs_full_build ]]; then
  rm -f data/.needs_full_build
  echo "$(date -u +%FT%TZ) new entity — running the full publish"
  exec ./run.sh
fi

$PY ui/build.py --live-only

# Only the blocks readers actually poll. The heavy per-fund and per-ticker files
# change on the daily cycle, not this one.
gcloud storage cp -Z ui/data/live.json ui/data/live-meta.json \
  "$WEB_BUCKET/data/" --content-type=application/json \
  --cache-control="no-cache, max-age=0, must-revalidate" --quiet
echo "$(date -u +%FT%TZ) published live data"
