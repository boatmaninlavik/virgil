"""Static server for the page, plus the one endpoint it needs.

The page is otherwise a flat file, but "add this investor" has to reach the
poller somehow, and a static host cannot take a POST. This serves ui/ exactly
as http.server did and accepts POST /api/add, appending the request to
data/requests.json for ingest/add_entity.py to pick up on its next cycle.

Deliberately the smallest thing that closes the loop: one route, a file for a
queue, no framework. On Vercel this becomes a serverless function writing to
the same JSON; nothing else about the page changes.

    python3 ui/serve.py [port]
"""
import json, os, sys, time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "ui")
QUEUE = os.path.join(ROOT, "data", "requests.json")
MAX_BODY = 8192


def load_queue():
    try:
        return json.load(open(QUEUE))
    except Exception:
        return []


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=UI, **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/requests"):
            return self._json(200, load_queue())
        return super().do_GET()

    def do_POST(self):
        if not self.path.startswith("/api/add"):
            return self._json(404, {"error": "no such endpoint"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._json(413, {"error": "too large"})
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"error": "bad request"})

        cik = str(req.get("cik") or "").strip()
        if not cik.isdigit():
            return self._json(400, {"error": "cik required"})
        cik = cik.zfill(10)

        q = load_queue()
        if any(r.get("cik") == cik for r in q):
            return self._json(200, {"status": "already queued", "cik": cik})
        q.append({"cik": cik,
                  "name": str(req.get("name") or "")[:120],
                  "label": str(req.get("label") or "")[:120],
                  "requested": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "state": "pending"})
        tmp = QUEUE + ".tmp"
        json.dump(q, open(tmp, "w"), indent=1)
        os.replace(tmp, QUEUE)
        return self._json(200, {"status": "queued", "cik": cik})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
    print(f"virgil on http://localhost:{port}  (queue: {QUEUE})")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
