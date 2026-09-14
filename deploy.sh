#!/bin/zsh
# Publish the built page to Vercel.
#
# The repo deliberately does not carry ui/index.html or ui/data — several MB of
# churn a day would be gigabytes of git objects within a year — so the site is
# uploaded straight from here rather than built by Vercel from a push. The CLI
# content-hashes every file and skips what it already has, so a routine publish
# moves only the handful of JSON blocks that actually changed.
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

if ! command -v vercel >/dev/null; then
  echo "vercel CLI not installed: brew install vercel-cli" >&2; exit 1
fi
# Token lives in ~/.virgil/credentials, outside the tree, so it is never
# committed and the unattended poller can deploy without an interactive login.
CREDS="$HOME/.virgil/credentials"
TOKEN="$(grep -m1 '^VERCEL_TOKEN=' "$CREDS" 2>/dev/null | cut -d= -f2-)"
if [[ -n "$TOKEN" ]]; then
  set -- --token "$TOKEN" "$@"
elif ! vercel whoami >/dev/null 2>&1; then
  echo "no credentials: run 'vercel login', or add VERCEL_TOKEN to $CREDS" >&2
  exit 1
fi
[[ -f ui/index.html ]] || { echo "nothing built yet - run ui/build.py" >&2; exit 1; }

vercel deploy --prod --yes --archive=tgz "$@"
