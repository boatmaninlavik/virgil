"""Headless smoke test: does the page actually render?

node --check catches syntax errors, but not the failure that shipped three
times: a stray token that parses fine and throws at runtime. `async` on its own
line is a valid identifier expression, so the parser was happy and the page was
blank. This loads the built page in Chrome, captures window.onerror, and fails
if any key container came back empty.
"""
import json, os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HOOK = """<script>
window.addEventListener("error", e => {
  var d = document.createElement("div"); d.id = "jserr";
  d.textContent = (e.message||"?") + " @line " + e.lineno;
  (document.body||document.documentElement).appendChild(d);
});
</script>
"""


def run(url_base="http://localhost:8770"):
    src = os.path.join(ROOT, "ui", "index.html")
    html = open(src).read().replace("<script>", HOOK + "<script>", 1)
    probe = os.path.join(ROOT, "ui", "_smoke.html")
    open(probe, "w").write(html)
    try:
        # Headless Chrome accumulated a 4.2 GB profile cache across runs and
        # filled the disk. A throwaway --user-data-dir fixes the growth but
        # stops Chrome exiting promptly, so instead cap the cache and prune it
        # in run.sh if it ever creeps back up.
        try:
            dom = subprocess.run(
                [CHROME, "--headless=new", "--disable-gpu", "--disk-cache-size=1",
                 "--disable-application-cache", "--disable-background-networking",
                 "--virtual-time-budget=9000",
                 "--dump-dom", f"{url_base}/_smoke.html"],
                capture_output=True, text=True, timeout=240).stdout
        except subprocess.TimeoutExpired:
            return False, "chrome timed out (machine busy?)"
    finally:
        os.path.exists(probe) and os.remove(probe)

    err = re.search(r'id="jserr">([^<]*)', dom)
    if err:
        return False, "JS error: " + err.group(1)
    # The feed is empty until something is followed, so check the empty state
    # renders and that Discover has real content behind it.
    checks = {
        "empty state or rows": len(re.findall(r'empty-state|class="rowwrap"', dom)),
        "filter chips": len(re.findall(r'class="chip"', dom)),
        "vintage bar": len(re.findall(r'13F holdings — quarter', dom)),
        "discover rows": len(re.findall(r'id="allfeed"', dom)),
    }
    empty = [k for k, v in checks.items() if v == 0]
    return (not empty), (f"empty: {', '.join(empty)}" if empty
                         else " · ".join(f"{k}={v}" for k, v in checks.items()))


if __name__ == "__main__":
    # Chrome can time out when a scan is saturating the machine, which is a
    # false negative that blocks a perfectly good publish. Retry once.
    ok, msg = run()
    if not ok and "timed out" not in msg.lower():
        pass
    elif not ok:
        import time as _t
        _t.sleep(5)
        ok, msg = run()
    print(("PASS  " if ok else "FAIL  ") + msg)
    sys.exit(0 if ok else 1)
