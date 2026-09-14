#!/bin/zsh
# Remove the local working set. Everything it deletes is in Cloud Storage and
# comes back with ./restore.sh; nothing here is the only copy.
set -euo pipefail
cd "$(dirname "$0")"
before=$(du -sm . | cut -f1)
rm -rf data ui/data ui/_smoke.html ui/_probe.html
after=$(du -sm . | cut -f1)
echo "freed $((before - after)) MB — state remains in gs://virgil-edgar/state"
