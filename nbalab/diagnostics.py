"""Data Source Diagnostics: run the live provider probe with progress, explain terms, and export a report.

One engine, two front ends: the standalone `NBA-Source-Diagnostics.exe` (nbalab/diag_app.py) and the Diagnostics panel inside the
Research Lab (routes under /api/diagnostics/*). Adding a provider to the router (and PROBE_PLAN) makes it testable here.

Honesty rules: outcomes are exactly what the network returned (ok / empty / timeout / blocked / http / auth / parse / unreachable /
not_configured / skipped). Nothing is retried with different identities, no proxies, no evasion. The report contains no IP address,
user name or file paths; response samples are trimmed public statistics.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .sources.router import PROBE_PLAN, SourceRouter
from .store import Store

DEFAULT_SOURCES = ["NBA_STATS", "NBA_LIVE", "NBA_INJURY_REPORT"]
KEYED = ["BALLDONTLIE", "SPORTSDATAIO"]
PLAIN = {
    "ok": "Connected", "empty": "Connected, no rows", "timeout": "Timed out", "blocked": "Blocked by the server", "unreachable": "Could not connect",
    "http": "Server returned an error", "auth": "Key rejected", "parse": "Reply not understood", "not_configured": "Needs setup", "skipped": "Skipped",
    "not_enabled": "Not enabled", "unsupported": "Not supported", "pending": "Waiting", "running": "Testing...",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def downloads_dir() -> str:
    home = os.path.expanduser("~")
    d = os.path.join(home, "Downloads")
    return d if os.path.isdir(d) else home


class DiagnosticsRunner:
    def __init__(self, router: Optional[SourceRouter] = None, app_name: str = "NBA Source Diagnostics"):
        self.router = router or SourceRouter(Store(":memory:"))
        self.app_name = app_name
        self.lock = threading.Lock()
        self.status = "idle"            # idle | running | done
        self.steps: List[dict] = []
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.acks: Dict[str, dict] = {}
        self.results: List[dict] = []
        self.last_export: Optional[str] = None
        self.last_ping = time.time()
        self.error: Optional[str] = None

    # ---- description ---------------------------------------------------------------------------
    def sources_info(self) -> List[dict]:
        out = []
        for n in DEFAULT_SOURCES + KEYED:
            s = self.router.sources.get(n)
            if not s:
                continue
            key_set = bool(s.key_env and os.environ.get(s.key_env))
            if n in KEYED and not key_set:
                continue
            out.append({"name": n, "kind": s.kind, "homepage": s.homepage, "terms_url": s.terms_url, "terms_summary": s.terms_summary, "terms_status": s.terms_status,
                        "needs_key": s.requires_key, "key_env": s.key_env, "key_set": key_set, "plan": PROBE_PLAN.get(n, []), "default": n in DEFAULT_SOURCES})
        return out

    def environment(self) -> dict:
        return {"app": self.app_name, "version": __version__, "platform": platform.platform(), "machine": platform.machine(),
                "python": platform.python_version(), "frozen": bool(getattr(sys, "frozen", False)),
                "pypdf_available": importlib.util.find_spec("pypdf") is not None,
                "local_utc_offset_min": round(-time.timezone / 60) if not time.localtime().tm_isdst else round(-time.altzone / 60)}

    # ---- running -------------------------------------------------------------------------------
    def start(self, names: List[str], acknowledged: List[str]) -> dict:
        with self.lock:
            if self.status == "running":
                raise ValueError("a test run is already in progress")
        known = {s["name"] for s in self.sources_info()}
        names = [n for n in names if n in known]
        if not names:
            raise ValueError("choose at least one source")
        missing = [n for n in names if n not in acknowledged]
        if missing:
            raise ValueError("terms acknowledgement is required before testing: " + ", ".join(missing))
        for n in names:
            s = self.router.sources[n]
            self.router.enable(n, f"I have read {s.terms_url} and accept responsibility for how I use this source")
            self.acks[n] = {"terms_url": s.terms_url, "acknowledged_at": _now(), "by": "owner ticked the confirmation box"}
        with self.lock:
            self.steps = [{"source": n, "capability": c, "state": "pending", "label": PLAIN["pending"], "rows": 0, "elapsed_ms": 0, "detail": ""} for n in names for c in PROBE_PLAN.get(n, [])]
            self.results, self.status, self.error = [], "running", None
            self.started_at, self.finished_at = _now(), None
        threading.Thread(target=self._work, args=(names,), daemon=True).start()
        return {"started": True, "steps": len(self.steps)}

    def _step(self, source: str, cap: str) -> Optional[dict]:
        for st in self.steps:
            if st["source"] == source and st["capability"] == cap:
                return st
        return None

    def _on_event(self, ev: dict) -> None:
        with self.lock:
            st = self._step(ev["source"], ev["capability"])
            if st is None:                                   # e.g. "(all)" for a source that could not be started
                st = {"source": ev["source"], "capability": ev["capability"], "state": "pending", "rows": 0, "elapsed_ms": 0, "detail": "", "label": ""}
                self.steps.append(st)
            if ev["phase"] == "start":
                st.update(state="running", label=PLAIN["running"], t0=time.time())
            else:
                st.update(state=ev["outcome"], label=PLAIN.get(ev["outcome"], ev["outcome"]), rows=ev["rows"], elapsed_ms=round(ev["elapsed_ms"] or 0) or round((time.time() - st.get("t0", time.time())) * 1000),
                          detail=ev["detail"], url=ev.get("url", ""))
                self.results.append({**{k: ev[k] for k in ("source", "capability", "outcome", "rows", "elapsed_ms", "detail", "url")}, "plain": st["label"], "sample_text": ev.get("sample_text")})

    def _work(self, names: List[str]) -> None:
        try:
            self.router.probe(names, progress=self._on_event)
        except Exception as e:                               # a bug must show up in the report, not vanish
            with self.lock:
                self.error = f"{type(e).__name__}: {e}"
        with self.lock:
            for st in self.steps:
                if st["state"] in ("pending", "running"):
                    st.update(state="skipped", label=PLAIN["skipped"], detail=st["detail"] or "not reached (an earlier step gave nothing to build on)")
                    self.results.append({"source": st["source"], "capability": st["capability"], "outcome": "skipped", "rows": 0, "elapsed_ms": 0, "detail": st["detail"], "url": "", "plain": PLAIN["skipped"], "sample_text": None})
            self.status, self.finished_at = "done", _now()

    def progress(self) -> dict:
        with self.lock:
            steps = [{k: v for k, v in st.items() if k != "t0"} for st in self.steps]
            for st, raw in zip(steps, self.steps):
                if raw["state"] == "running":
                    st["elapsed_ms"] = round((time.time() - raw.get("t0", time.time())) * 1000)
            done = sum(1 for st in steps if st["state"] not in ("pending", "running"))
            per: Dict[str, dict] = {}
            for st in steps:
                p = per.setdefault(st["source"], {"ok": 0, "total": 0, "timeout": 0, "problems": 0})
                p["total"] += 1
                p["ok"] += st["state"] in ("ok", "empty")
                p["timeout"] += st["state"] == "timeout"
                p["problems"] += st["state"] not in ("ok", "empty", "pending", "running", "skipped")
            return {"status": self.status, "steps": steps, "done": done, "total": len(steps), "per_source": per, "started_at": self.started_at,
                    "finished_at": self.finished_at, "error": self.error, "last_export": self.last_export}

    # ---- report --------------------------------------------------------------------------------
    def build_report(self) -> dict:
        with self.lock:
            results = [dict(r) for r in self.results]
        by: Dict[str, int] = {}
        for r in results:
            by[r["outcome"]] = by.get(r["outcome"], 0) + 1
        per_source = {}
        for r in results:
            p = per_source.setdefault(r["source"], {"ok": 0, "total": 0})
            p["total"] += 1
            p["ok"] += r["outcome"] in ("ok", "empty")
        return {"report_version": 1, "generated_at": _now(), "started_at": self.started_at, "finished_at": self.finished_at, "environment": self.environment(),
                "terms_acknowledgements": self.acks, "summary": {"ok": sum(1 for r in results if r["outcome"] in ("ok", "empty")), "total": len(results), "by_outcome": by, "per_source": per_source},
                "results": results, "error": self.error,
                "notes": ["Outcomes are what the network returned from the machine that ran this test; a timeout from a cloud host is not proof an endpoint fails at home.",
                          "sample_text holds a trimmed raw sample of each successful reply (public statistics only).", "No IP address, user name or file path is recorded."]}

    def export(self, folder: Optional[str] = None) -> dict:
        if self.status != "done":
            raise ValueError("run the tests first; there is nothing to export yet")
        folder = folder or downloads_dir()
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "probe_report.json")
        data = json.dumps(self.build_report(), indent=1, default=str)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        self.last_export = path
        return {"path": path, "folder": folder, "bytes": len(data.encode("utf-8"))}

    def reveal(self) -> dict:
        if not self.last_export or not os.path.exists(self.last_export):
            raise ValueError("export the report first")
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.last_export)])
        return {"path": self.last_export, "opened": sys.platform == "win32"}

    # ---- shared route table (used by the standalone app and the Lab server) ---------------------------
    def handle(self, method: str, route: str, body: Optional[dict] = None) -> Tuple[int, Any]:
        body = body or {}
        if method == "GET" and route == "state":
            return 200, {"sources": self.sources_info(), "environment": self.environment(), "downloads": downloads_dir(), "plain": PLAIN, "progress": self.progress()}
        if method == "GET" and route == "progress":
            return 200, self.progress()
        if method == "GET" and route == "ping":
            self.last_ping = time.time()
            return 200, {"ok": True}
        if method == "POST" and route == "start":
            return 200, self.start(body.get("sources", []), body.get("acknowledged", []))
        if method == "POST" and route == "export":
            return 200, self.export(body.get("folder"))
        if method == "POST" and route == "reveal":
            return 200, self.reveal()
        if method == "GET" and route == "report":
            return 200, self.build_report()
        return 404, {"error": "unknown diagnostics route"}
