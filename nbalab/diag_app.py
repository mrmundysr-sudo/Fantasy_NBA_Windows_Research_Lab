"""Standalone Data Source Diagnostics app (NBA-Source-Diagnostics.exe).

Starts a loopback-only web UI (random port, per-launch token, Host check, strict CSP, no CORS) and opens it in the default browser.
No admin rights, no installer, nothing written except the exported report. Exits when the browser tab has been gone for a while.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .diag_page import PAGE
from .diagnostics import DiagnosticsRunner

IDLE_EXIT_S = 60.0
CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


class DiagServer:
    def __init__(self, runner: DiagnosticsRunner, port: int = 0):
        self.runner, self.token = runner, secrets.token_urlsafe(24)
        self.quit_event = threading.Event()
        app = self

        class H(BaseHTTPRequestHandler):
            server_version = "NBADiag"

            def log_message(self, *a):
                pass

            def _send(self, code, body: bytes, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype + "; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", CSP)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code, obj):
                self._send(code, json.dumps(obj, default=str).encode("utf-8"))

            def _host_ok(self):
                return (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")

            def _handle(self, method):
                if not self._host_ok():
                    return self._json(403, {"error": "bad host header"})
                path, _, query = self.path.partition("?")
                if method == "GET" and path == "/":
                    if f"t={app.token}" not in query:
                        return self._send(401, b"Open this program from its own window (token required).", "text/plain")
                    return self._send(200, PAGE.replace("__TOKEN__", app.token).encode("utf-8"), "text/html")
                if not path.startswith("/api/diag/"):
                    return self._json(404, {"error": "not found"})
                if not secrets.compare_digest(self.headers.get("X-Token", ""), app.token):
                    return self._json(401, {"error": "missing or invalid token"})
                route = path[len("/api/diag/"):]
                body = {}
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > 100_000:
                        return self._json(413, {"error": "too large"})
                    body = json.loads(self.rfile.read(n) or b"{}")
                try:
                    if route == "quit" and method == "POST":
                        self._json(200, {"ok": True})
                        app.quit_event.set()
                        return
                    code, out = app.runner.handle(method, route, body)
                    self._json(code, out)
                except ValueError as e:
                    self._json(400, {"error": str(e)})
                except Exception as e:
                    self._json(500, {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1200:]})

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def start(self) -> None:
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def wait(self, idle_exit_s: float = IDLE_EXIT_S) -> None:
        """Block until Quit is pressed or the page has stopped pinging (tab closed). Never exits while a test run is in progress."""
        seen = False
        t0 = time.time()
        while not self.quit_event.wait(1.0):
            if self.runner.last_ping > t0:
                seen = True
            if self.runner.status == "running":
                self.runner.last_ping = max(self.runner.last_ping, time.time() - idle_exit_s / 2)
                continue
            if (seen and time.time() - self.runner.last_ping > idle_exit_s) or (not seen and time.time() - t0 > 300):
                break
        self.httpd.shutdown()


def _message(title: str, text: str) -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
            return
        except Exception:
            pass
    print(f"{title}: {text}", file=sys.stderr)


def selftest() -> int:
    """No network: a recorded transport answers every request with a timeout. Proves server, gate, run, progress and export work in the packaged exe."""
    import tempfile
    import urllib.request
    from .sources.base import SourceError
    from .sources.router import SourceRouter
    from .sources.transport import RecordedTransport
    from .store import Store
    runner = DiagnosticsRunner(SourceRouter(Store(":memory:"), transport=RecordedTransport({"nba.com": SourceError("timeout", "selftest: simulated timeout")})))
    srv = DiagServer(runner)
    srv.start()

    def call(route, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{srv.port}/api/diag/{route}", data=json.dumps(body).encode() if body is not None else None, headers={"X-Token": srv.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    html = urllib.request.urlopen(srv.url, timeout=10).read().decode()
    assert "NBA Source Diagnostics" in html and srv.token in html
    code, st = call("state")
    assert code == 200 and [s["name"] for s in st["sources"]][:3] == ["NBA_STATS", "NBA_LIVE", "NBA_INJURY_REPORT"], st
    code, out = call("start", {"sources": ["NBA_STATS"], "acknowledged": []})
    assert code == 400 and "acknowledgement" in out["error"], (code, out)
    assert call("start", {"sources": ["NBA_STATS", "NBA_LIVE", "NBA_INJURY_REPORT"], "acknowledged": ["NBA_STATS", "NBA_LIVE", "NBA_INJURY_REPORT"]})[0] == 200
    for _ in range(100):
        p = call("progress")[1]
        if p["status"] == "done":
            break
        time.sleep(0.2)
    assert p["status"] == "done" and p["done"] == p["total"], p
    folder = tempfile.mkdtemp()
    code, ex = call("export", {"folder": folder})
    rep = json.load(open(ex["path"], encoding="utf-8"))
    assert code == 200 and rep["summary"]["total"] == p["total"] and rep["terms_acknowledgements"], rep["summary"]
    print(f"SELFTEST OK: {p['total']} checks, export {ex['bytes']} bytes, frozen={getattr(sys, 'frozen', False)}, pypdf={rep['environment']['pypdf_available']}")
    return 0


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if "--selftest" in argv:
            out = argv[argv.index("--selftest-out") + 1] if "--selftest-out" in argv else None
            import contextlib, io
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    code = selftest()
            except Exception:
                buf.write(traceback.format_exc())
                code = 1
            if out:                      # the windowed exe has no console: report through a file
                with open(out, "w", encoding="utf-8") as f:
                    f.write(buf.getvalue() + f"\nexit={code}\n")
            else:
                print(buf.getvalue())
            return code
        srv = DiagServer(DiagnosticsRunner())
        srv.start()
        if "--no-browser" not in argv:
            webbrowser.open(srv.url)
        else:
            print(srv.url)
        srv.wait()
        return 0
    except Exception as e:
        _message("NBA Source Diagnostics could not start", f"{type(e).__name__}: {e}")
        return 1
