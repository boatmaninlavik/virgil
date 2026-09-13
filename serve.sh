#!/bin/zsh
# Serve the dashboard over HTTP so the page can lazy-load its data files.
# Opening index.html directly (file://) makes the browser block those fetches,
# which is why "show all positions" and ticker detail need this.
cd "$(dirname "$0")/ui"
PORT="${PORT:-8770}"
echo "Virgil -> http://localhost:$PORT/"
exec python3 -m http.server "$PORT" --bind 127.0.0.1
