#!/bin/zsh
# Pull the working set back down from Cloud Storage.
#
# Nothing here is kept locally any more: the pipeline runs in GitHub Actions
# with its state in gs://virgil-edgar/state, and ui/data is served from
# gs://virgil-web. Run this only to work on the pipeline by hand — and run
# ./purge.sh afterwards rather than leaving a hundred megabytes sitting around.
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

# Refuse to pull over an existing working set. rsync overwrites whatever it
# finds, and running this on a populated data/ silently replaced several hours
# of backfilled price history with the older copies from the bucket.
if [[ "${1:-}" != "--force" ]] && [[ -d data ]] && \
   [[ -n "$(ls -A data 2>/dev/null)" ]]; then
  echo "data/ is not empty — restoring would overwrite it." >&2
  echo "Push your changes first, or rerun with --force to discard them." >&2
  exit 1
fi
echo "restoring data/ from gs://virgil-edgar/state ..."
mkdir -p data && gcloud storage rsync -r gs://virgil-edgar/state data --quiet
echo "restoring ui/data/ from gs://virgil-web/data ..."
mkdir -p ui/data && gcloud storage rsync -r gs://virgil-web/data ui/data --quiet
du -sh data ui/data
