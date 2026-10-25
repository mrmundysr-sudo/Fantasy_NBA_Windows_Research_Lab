"""Launcher: starts the local server and opens a desktop window (pywebview/WebView2) or the default browser."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

from . import __version__
from .lab import Lab
from .server import App


def selftest(app: App) -> int:
    """Headless smoke test used by CI on the packaged exe: hit key endpoints and the UI shell."""
    base = f"http://127.0.0.1:{app.port}"
    hdr = {"X-Lab-Token": app.token}

    def get(path):
        req = urllib.request.Request(base + path, headers=hdr)
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()

    checks = []
    html = get("/?t=" + app.token).decode()
    checks.append(("index served with token", app.token in html and "__LAB_TOKEN__" not in html))
    for f in ("app.js", "panels_a.js", "panels_b.js", "boot.js", "style.css"):
        checks.append(("static " + f, len(get("/" + f)) > 500))
    st = json.loads(get("/api/status"))
    checks.append(("status synthetic labelled", st["synthetic"] and st["dataset_label"] == "SYNTHETIC"))
    q = {"entity": "player", "metrics": [{"id": "pts", "transform": "per36"}], "min_minutes": 300, "limit": 5}
    req = urllib.request.Request(base + "/api/workbench", data=json.dumps({"query": q}).encode(), headers={**hdr, "Content-Type": "application/json"})
    res = json.loads(urllib.request.urlopen(req, timeout=120).read())
    checks.append(("workbench query", len(res["rows"]) == 5))
    picks = json.loads(get("/api/picks?limit=5"))
    checks.append(("picks projected", len(picks["rows"]) > 0))
    bad = False
    try:
        urllib.request.urlopen(base + "/api/status", timeout=10)
    except Exception:
        bad = True
    checks.append(("token required", bad))
    ok = True
    for name, passed in checks:
        print(("PASS " if passed else "FAIL ") + name)
        ok &= bool(passed)
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nbalab", description="NBA Research Lab (private Windows research workstation)")
    ap.add_argument("--db", help="database path (default: %%LOCALAPPDATA%%\\NBALab\\lab.sqlite)")
    ap.add_argument("--selftest", action="store_true", help="start headless, run smoke checks, exit")
    ap.add_argument("--browser", action="store_true", help="open in the default browser instead of a desktop window")
    ap.add_argument("--no-open", action="store_true", help="just serve (prints URL)")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)
    print("Opening lab (first run builds the SYNTHETIC fixture; this takes a few seconds)...", flush=True)
    lab = Lab.open(a.db)
    app = App(lab, port=a.port)
    app.start()
    try:
        if a.selftest:
            return selftest(app)
        print("NBA Research Lab running at", app.url, flush=True)
        if a.no_open:
            while True:
                time.sleep(3600)
        if not a.browser:
            try:
                import webview  # pywebview -> WebView2 on Windows
                webview.create_window("NBA Research Lab", app.url, width=1500, height=900, min_size=(1000, 650))
                webview.start()
                return 0
            except Exception as e:  # not installed or no runtime: fall back
                print("Desktop window unavailable (" + type(e).__name__ + "), using browser.", file=sys.stderr)
        import webbrowser
        webbrowser.open(app.url)
        print("Press Ctrl+C to quit.")
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0
    finally:
        app.stop()


if __name__ == "__main__":
    sys.exit(main())
