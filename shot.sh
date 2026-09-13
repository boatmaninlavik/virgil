#!/bin/zsh
# Screenshot helper with a disposable Chrome profile.
# Headless Chrome otherwise accumulates a profile cache per run - that reached
# 4.2 GB and filled the disk mid-build.
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROF=$(mktemp -d /tmp/virgil-chrome-XXXX)
trap 'rm -rf "$PROF"' EXIT
"$CHROME" --headless=new --disable-gpu --hide-scrollbars \
  --user-data-dir="$PROF" --disk-cache-size=1 --no-first-run \
  --no-default-browser-check --disable-background-networking \
  --disable-extensions --disable-sync --disable-component-update \
  --virtual-time-budget="${BUDGET:-9000}" --window-size="${SIZE:-1280,900}" \
  --screenshot="$1" "$2" 2>/dev/null
