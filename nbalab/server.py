"""Local JSON API + static UI server (standard library only).

Security model for a desktop app talking to itself:
  * binds to 127.0.0.1 only;
  * every /api request needs the per-launch token (embedded in the page we serve);
  * the Host header must be localhost/127.0.0.1 with our port (blocks DNS rebinding);
  * no CORS headers are ever sent, so other web pages cannot read responses.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import secrets
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import numpy as np

from . import __version__, audit, explorer, registry
from .engine import QueryError
from .lab import Lab
from .providers.adapters import CsvEventProvider
from .sources.base import SourceError
from .store import default_data_dir, utcnow


def ui_dir() -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        for sub in ("ui_data", os.path.join("nbalab", "ui")):
            p = os.path.join(base, sub)
            if os.path.isdir(p):
                return p
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


def clean(o: Any):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, bytes):
        return None
    return o


MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json"}


def data_databases():
    """SQLite lab databases in the data directory with their dataset label (read-only peek)."""
    import sqlite3
    out = []
    d = default_data_dir()
    for f in sorted(os.listdir(d)):
        if f.endswith(".sqlite"):
            p = os.path.join(d, f)
            label = None
            try:
                c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                r = c.execute("SELECT value FROM meta WHERE key='data_label'").fetchone()
                label = json.loads(r[0]) if r else None
                c.close()
            except Exception:
                pass
            out.append({"path": p, "name": f, "label": label, "size_mb": round(os.path.getsize(p) / 1e6, 1)})
    return out


class App:
    def __init__(self, lab: Lab, port: int = 0, token: Optional[str] = None):
        self.lab = lab
        self.token = token or secrets.token_urlsafe(24)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.port = self.httpd.server_address[1]
        self.thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"


    def start_job(self, kind: str, fn) -> str:
        """Run fn(say) in a thread; state/progress/result/error are readable at /api/jobs/<id>."""
        import time as _t
        lab = self.lab
        jid = f"{kind}-{int(_t.time() * 1000)}"
        job = {"id": jid, "kind": kind, "state": "running", "progress": [], "result": None, "error": None}
        lab.jobs[jid] = job

        def run():
            try:
                job["result"] = fn(lambda m: job["progress"].append(m))
                job["state"] = "done"
            except Exception as e:  # noqa: BLE001 - surfaced to the UI, never crash the server
                job["error"] = f"{type(e).__name__}: {e}"
                job["state"] = "error"
        threading.Thread(target=run, daemon=True).start()
        return jid

    def swap_lab(self, lab) -> None:
        old = self.lab
        self.lab = lab
        try:
            old.store.close()
        except Exception:
            pass

    def start(self) -> None:
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # -------------------------------------------------------------------------------------------
    def _handler(self):
        app = self

        class H(BaseHTTPRequestHandler):
            server_version = "NBALab/" + __version__

            def log_message(self, *a):  # keep the console quiet
                pass

            def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, obj: Any) -> None:
                self._send(code, json.dumps(clean(obj)).encode("utf-8"))

            def _host_ok(self) -> bool:
                host = (self.headers.get("Host") or "").lower()
                return host in (f"127.0.0.1:{app.port}", f"localhost:{app.port}")

            def _auth_ok(self, qs) -> bool:
                tok = self.headers.get("X-Lab-Token") or (qs.get("t") or [""])[0]
                return secrets.compare_digest(tok, app.token)

            def _body(self) -> dict:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 8_000_000:
                    raise ValueError("request too large")
                raw = self.rfile.read(n) if n else b"{}"
                return json.loads(raw.decode("utf-8") or "{}")

            def do_GET(self):
                self._dispatch("GET")

            def do_POST(self):
                self._dispatch("POST")

            def do_DELETE(self):
                self._dispatch("DELETE")

            def _dispatch(self, method: str):
                u = urlparse(self.path)
                qs = parse_qs(u.query)
                if not self._host_ok():
                    return self._json(403, {"error": "bad host header"})
                path = u.path
                if not path.startswith("/api/"):
                    return self._static(path, qs)
                if not self._auth_ok(qs):
                    return self._json(401, {"error": "missing or invalid token"})
                try:
                    body = self._body() if method in ("POST", "DELETE") else {}
                    code, out = app.route(method, path, qs, body)
                    self._json(code, out)
                except QueryError as e:
                    self._json(400, {"error": str(e), "kind": type(e).__name__})
                except SourceError as e:
                    self._json(400, {"error": str(e), "kind": e.kind})
                except KeyError as e:
                    self._json(404, {"error": f"not found: {e}"})
                except (ValueError, json.JSONDecodeError) as e:
                    self._json(400, {"error": str(e)})
                except Exception as e:  # pragma: no cover
                    self._json(500, {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]})

            def _static(self, path, qs):
                if path in ("/", "/index.html"):
                    if not self._auth_ok(qs):
                        return self._send(401, b"Open the app from the launcher (token required).", "text/plain")
                    html = open(os.path.join(ui_dir(), "index.html"), encoding="utf-8").read().replace("__LAB_TOKEN__", app.token)
                    return self._send(200, html.encode("utf-8"), MIME[".html"])
                name = os.path.basename(path)
                full = os.path.join(ui_dir(), name)
                ext = os.path.splitext(name)[1]
                if ext not in MIME or not os.path.isfile(full) or name == "index.html":
                    return self._send(404, b"not found", "text/plain")
                return self._send(200, open(full, "rb").read(), MIME[ext])

        return H

    # -------------------------------------------------------------------------------------------
    def route(self, method: str, path: str, qs, body: dict):
        lab = self.lab
        g = lambda k, d=None: (qs.get(k) or [d])[0]
        if method == "GET":
            if path == "/api/status":
                return 200, lab.status()
            if path == "/api/metrics":
                q = g("q", "")
                items = registry.search(q, 500) if q else registry.all_metrics()
                cat, kind = g("category"), g("kind")
                return 200, {"metrics": [m.to_dict() for m in items if (not cat or m.category == cat) and (not kind or m.kind == kind)],
                             "kinds": list(registry.KINDS), "dataset_label": lab.ds.label()}
            if path == "/api/search":
                return 200, explorer.search(lab, g("q", ""))
            if path == "/api/entities":
                et = g("type", "player")
                ds = lab.ds
                if et == "player":
                    rows = [{"id": i, "name": p["name"], "team": ds.teams[p["team_id"]]["abbr"], "position": p["position"]} for i, p in ds.players.items()]
                elif et == "team":
                    rows = [{"id": i, "name": t["name"], "abbr": t["abbr"]} for i, t in ds.teams.items()]
                elif et == "coach":
                    rows = [{"id": i, "name": c["name"]} for i, c in ds.coaches.items()]
                else:
                    rows = [{"id": i, "name": n} for i, n in ds.lineup_names.items()]
                q = g("q", "").lower()
                return 200, {"entities": [r for r in rows if q in r["name"].lower()][:int(g("limit", "300"))], "seasons": ds.seasons}
            if path == "/api/picks":
                sc = lab.scoring.get(g("scoring", "classic_points"))
                lab.ensure_projections()
                return 200, lab.projector.picks(sc, g("category", "overall"), g("q", ""), int(g("days", "1")), int(g("team")) if g("team") else None,
                                                g("position"), float(g("min_p_play", "0")), int(g("limit", "40")))
            if path == "/api/scoring":
                return 200, {"systems": lab.scoring.list()}
            if path.startswith("/api/player/"):
                lab.ensure_projections()
                return 200, explorer.player_profile(lab, int(path.rsplit("/", 1)[1]))
            if path.startswith("/api/team/"):
                return 200, explorer.team_profile(lab, int(path.rsplit("/", 1)[1]))
            if path == "/api/events":
                return 200, {"events": lab.events.recent_events(int(g("limit", "80"))), "clock": lab.clock()}
            if path == "/api/sources":
                return 200, {"sources": lab.events.source_status(), "clock": lab.clock(), "adapters": [a.verify() for a in lab.events.adapters.values()]}
            if path == "/api/conflicts":
                return 200, {"conflicts": lab.events.conflicts()}
            if path == "/api/models":
                return 200, {"champion": lab.registry.champion(), "stack": lab.registry.champion_stack(), "history": lab.registry.history(60),
                             "last_tournament": lab.store.get_meta("last_tournament"), "trained_through": lab.trained_through,
                             "train_info": {k: {kk: vv for kk, vv in m.train_info.items() if kk not in ("play_table",)} for k, m in lab.models.items()}}
            if path.startswith("/api/tournament/"):
                job = lab.jobs.get(path.rsplit("/", 1)[1])
                if not job:
                    raise KeyError("job")
                return 200, {k: v for k, v in job.items() if k != "result"} | ({"result": job["result"]} if job["state"] == "done" else {})
            if path == "/api/audit":
                return 200, audit.full_report(lab, deep=g("deep", "1") == "1")
            if path == "/api/workspaces":
                rows = lab.store.query("SELECT name, spec_json, updated_at FROM workspaces ORDER BY name")
                return 200, {"workspaces": [{"name": r["name"], "spec": json.loads(r["spec_json"]), "updated_at": r["updated_at"]} for r in rows]}
            if path == "/api/datasources":
                return 200, {"sources": [x.describe() for x in lab.sources.sources.values()], "matrix": lab.sources.matrix(), "priority": lab.sources.priority,
                             "log": [dict(r) for r in lab.store.query("SELECT * FROM source_log ORDER BY log_id DESC LIMIT 40")],
                             "probes": [dict(r) for r in lab.store.query("SELECT probe_id, at, source, capability, outcome, rows, elapsed_ms, detail FROM source_probe ORDER BY probe_id DESC LIMIT 80")],
                             "dataset_label": lab.ds.label(), "synthetic": lab.ds.synthetic}
            if path.startswith("/api/jobs/"):
                job = lab.jobs.get(path.rsplit("/", 1)[1])
                if not job:
                    raise KeyError("job")
                return 200, {k: v for k, v in job.items()}
            if path == "/api/data/databases":
                return 200, {"databases": data_databases(), "current": getattr(lab.store, "path", None)}
            if path == "/api/nlq/examples":
                from .nlq import EXAMPLES
                a, b = list(lab.ds.players.values())[12]["name"], list(lab.ds.players.values())[108]["name"]
                return 200, {"examples": [e.replace("<player A>", a).replace("<player B>", b).replace("<player>", a).replace("<teammate>", b) for e in EXAMPLES]}
            return 404, {"error": "unknown route"}
        if method == "POST":
            if path == "/api/sources/enable":
                return 200, lab.sources.enable(body["name"], body.get("acknowledgement", ""))
            if path == "/api/sources/disable":
                return 200, lab.sources.disable(body["name"])
            if path == "/api/sources/priority":
                lab.sources.set_priority(body["capability"], body["order"])
                return 200, {"priority": lab.sources.priority[body["capability"]]}
            if path == "/api/sources/probe":
                names = body.get("names")
                save = os.path.join(os.path.dirname(os.path.abspath(lab.store.path)), "probe_samples") if lab.store.path != ":memory:" else None
                return 200, {"job_id": self.start_job("probe", lambda say: lab.sources.probe(names, save_dir=save))}
            if path == "/api/data/import":
                seasons = body.get("seasons") or [__import__("nbalab.sources", fromlist=["x"]).current_season()]
                target = body.get("path") or os.path.join(default_data_dir(), "nba_real.sqlite")

                def job(say):
                    from .store import Store
                    from .sources.importer import import_real
                    if os.path.exists(target) and not body.get("overwrite"):
                        raise ValueError(f"{target} already exists (pass overwrite=true to replace it)")
                    if os.path.exists(target):
                        os.remove(target)
                    st = Store(target)
                    try:
                        out = import_real(lab.sources, st, seasons, progress=say)
                    finally:
                        st.close()
                    out["path"] = target
                    return out
                return 200, {"job_id": self.start_job("import", job)}
            if path == "/api/data/switch":
                target = body["path"]
                if target not in [d["path"] for d in data_databases()]:
                    raise ValueError("unknown database (must be one of /api/data/databases)")
                self.swap_lab(Lab.open(target))
                return 200, {"switched": target}
            if path == "/api/workbench":
                return 200, lab.engine.run(body["query"])
            if path == "/api/support":
                return 200, lab.engine.support(body["query"], int(body["id"]), int(body.get("limit", 200)))
            if path == "/api/nlq":
                lab.ensure_projections()
                return 200, lab.nlq.answer(str(body.get("text", "")))
            if path == "/api/query/rerun":
                lab.ensure_projections()
                return 200, lab.nlq.rerun(body["query"])
            if path == "/api/scoring":
                return 200, lab.scoring.add_version(body["id"], body["name"], body["rules"])
            if path == "/api/projections/run":
                lab.train()
                return 200, lab.projector.project_upcoming(lab.clock(), int(body.get("days", 4)))
            if path == "/api/demo/deliver":
                return 200, lab.deliver_demo_report()
            if path == "/api/demo/reset":
                lab.reset_demo()
                return 200, {"ok": True, "note": "demo script rewound; stored events and append-only projections are kept"}
            if path == "/api/poll":
                return 200, lab.events.poll(body["source"])
            if path == "/api/events/upload":
                prov: CsvEventProvider = lab.csv_provider
                import tempfile
                fd, tmp = tempfile.mkstemp(suffix=".csv")
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                    f.write(body["csv"])
                prov.path = tmp
                try:
                    return 200, lab.events.poll("CSV_FILE")
                finally:
                    os.unlink(tmp)
            if path == "/api/costs/import":
                lab.ensure_projections()
                return 200, lab.projector.import_costs(body["rows"], body.get("source", "user import"))
            if path == "/api/tournament/start":
                lab.train()
                jid = lab.start_tournament(int(body.get("folds", 3)), int(body.get("draws", 80)), body.get("max_games_per_fold"))
                return 200, {"job_id": jid}
            if path == "/api/models/rollback":
                return 200, {"champion": lab.registry.rollback(body.get("note", "manual rollback"))}
            if path == "/api/workspaces":
                name = str(body["name"])[:80]
                lab.store.execute("INSERT OR REPLACE INTO workspaces (name, spec_json, updated_at) VALUES (?,?,?)", (name, json.dumps(body["spec"]), utcnow()))
                return 200, {"saved": name}
            return 404, {"error": "unknown route"}
        if method == "DELETE" and path == "/api/workspaces":
            lab.store.execute("DELETE FROM workspaces WHERE name=?", (str(body["name"]),))
            return 200, {"deleted": body["name"]}
        return 405, {"error": "method not allowed"}
