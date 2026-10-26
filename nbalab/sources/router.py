"""SourceRouter: owner-controlled enablement, capability routing with fallback, provenance log, probe results.

Priority example (default): player_game_logs -> [NBA_STATS, BALLDONTLIE, SPORTSDATAIO]; injuries -> [SPORTSDATAIO, BALLDONTLIE, NBA_LIVE].
`fetch` returns the first enabled source that succeeds; `fetch_all` returns every enabled source's result so records can be
combined (EventManager already resolves conflicting reports by latest publication time and flags them).
"""
from __future__ import annotations

import json
import platform
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .base import CAPABILITIES, DataSource, Fetched, SourceError
from .balldontlie import BallDontLieSource
from .nba_injury_report import NbaInjuryReportSource
from .nba_stats import NbaLiveSource, NbaStatsSource
from .sportsdataio import SportsDataIOSource

DEFAULT_PRIORITY: Dict[str, List[str]] = {
    "players_index": ["NBA_STATS", "BALLDONTLIE", "SPORTSDATAIO"],
    "schedule": ["NBA_STATS", "SPORTSDATAIO", "BALLDONTLIE"],
    "player_game_logs": ["NBA_STATS", "SPORTSDATAIO", "BALLDONTLIE"],
    "player_game_logs_advanced": ["NBA_STATS", "BALLDONTLIE"],
    "team_game_logs": ["NBA_STATS"],
    "player_season_stats": ["NBA_STATS", "SPORTSDATAIO", "BALLDONTLIE"],
    "player_season_advanced": ["NBA_STATS"],
    "team_season_advanced": ["NBA_STATS", "SPORTSDATAIO"],
    "lineup_combinations": ["NBA_STATS"],
    "shot_locations": ["NBA_STATS"],
    "shot_chart": ["NBA_STATS"],
    "box_score": ["NBA_STATS", "SPORTSDATAIO", "BALLDONTLIE"],
    "play_by_play": ["NBA_STATS", "SPORTSDATAIO", "BALLDONTLIE"],
    "live_scoreboard": ["NBA_LIVE"],
    "live_box_score": ["NBA_LIVE"],
    "injuries": ["NBA_INJURY_REPORT", "SPORTSDATAIO", "BALLDONTLIE", "NBA_LIVE"],
    "starting_lineups": ["SPORTSDATAIO", "NBA_LIVE", "BALLDONTLIE"],
    "odds": ["BALLDONTLIE"],
    "projections": ["SPORTSDATAIO"],
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def current_season(today: Optional[str] = None) -> str:
    d = (today or _now())[:10]
    y, m = int(d[:4]), int(d[5:7])
    start = y if m >= 9 else y - 1
    return f"{start}-{(start + 1) % 100:02d}"


class SourceRouter:
    def __init__(self, store, transport=None, sources: Optional[List[DataSource]] = None):
        self.store = store
        self.sources: Dict[str, DataSource] = {}
        for s in sources or [NbaStatsSource(transport), NbaLiveSource(transport), BallDontLieSource(transport), SportsDataIOSource(transport), NbaInjuryReportSource(transport)]:
            self.sources[s.name] = s
        self.priority = {k: list(v) for k, v in DEFAULT_PRIORITY.items()}
        saved = store.get_meta("source_priority")
        if saved:
            self.priority.update(saved)
        cfg = store.get_meta("source_config", {}) or {}
        for name, c in cfg.items():
            if name in self.sources and c.get("enabled") and c.get("ack"):
                self.sources[name].enabled, self.sources[name].ack = True, c["ack"]

    # ---- owner controls ----------------------------------------------------------------------
    def enable(self, name: str, acknowledgement: str) -> dict:
        s = self.sources[name]
        need = f"I have read {s.terms_url or s.name} and accept responsibility for how I use this source"
        if (acknowledgement or "").strip() != need:
            raise SourceError("not_enabled", "acknowledgement text must match exactly: " + need)
        ack = {"text": acknowledgement.strip(), "at": _now(), "terms_url": s.terms_url}
        s.enabled, s.ack = True, ack
        self._save_cfg()
        return {"name": name, "enabled": True, "ack": ack}

    def disable(self, name: str) -> dict:
        self.sources[name].enabled = False
        self._save_cfg()
        return {"name": name, "enabled": False}

    def _save_cfg(self) -> None:
        self.store.set_meta("source_config", {n: {"enabled": s.enabled, "ack": s.ack} for n, s in self.sources.items() if s.enabled})

    def set_priority(self, cap: str, order: List[str]) -> None:
        if cap not in CAPABILITIES or any(n not in self.sources for n in order):
            raise SourceError("unsupported", "unknown capability or source")
        self.priority[cap] = list(order)
        self.store.set_meta("source_priority", self.priority)

    # ---- fetching -----------------------------------------------------------------------------
    def _log(self, source: str, cap: str, ok: bool, kind: str = "", detail: str = "", rows: int = 0, ms: float = 0, url: str = "") -> None:
        self.store.execute("INSERT INTO source_log (at, source, capability, ok, error_kind, detail, rows, elapsed_ms, url) VALUES (?,?,?,?,?,?,?,?,?)",
                           (_now(), source, cap, 1 if ok else 0, kind, detail[:300], rows, ms, url))

    def fetch_from(self, name: str, cap: str, **params) -> Fetched:
        s = self.sources[name]
        try:
            f = s.fetch(cap, **params)
        except SourceError as e:
            self._log(name, cap, False, e.kind, e.detail)
            raise
        self._log(name, cap, True, rows=len(f.records), ms=f.meta.get("elapsed_ms", 0), url=f.meta.get("url", ""))
        return f

    def fetch(self, cap: str, **params) -> Fetched:
        errors = []
        for name in self.priority.get(cap, []):
            s = self.sources.get(name)
            if not s or not s.enabled or not s.supports(cap):
                continue
            try:
                f = self.fetch_from(name, cap, **params)
                f.meta["tried"] = errors
                return f
            except SourceError as e:
                errors.append({"source": name, "error": e.kind, "detail": e.detail})
        raise SourceError("not_enabled" if not errors else "unreachable", f"no enabled source could serve {cap}: {errors or 'none enabled'}")

    def fetch_all(self, cap: str, **params) -> List[Fetched]:
        out = []
        for name in self.priority.get(cap, []):
            s = self.sources.get(name)
            if s and s.enabled and s.supports(cap):
                try:
                    out.append(self.fetch_from(name, cap, **params))
                except SourceError:
                    pass
        return out

    # ---- capability matrix --------------------------------------------------------------------
    def last_probes(self) -> Dict[tuple, dict]:
        rows = self.store.query("SELECT p.* FROM source_probe p JOIN (SELECT MAX(probe_id) m FROM source_probe GROUP BY source, capability) x ON p.probe_id=x.m")
        return {(r["source"], r["capability"]): dict(r) for r in rows}

    def matrix(self) -> List[dict]:
        probes = self.last_probes()
        out = []
        for cap, desc in CAPABILITIES.items():
            cells = []
            for name, s in self.sources.items():
                ci = s.capabilities().get(cap)
                if not ci:
                    continue
                pr = probes.get((name, cap))
                cells.append({"source": name, "endpoint": ci.endpoint, "tier": ci.tier, "evidence": "probe_verified" if pr and pr["outcome"] == "ok" else ci.evidence, "notes": ci.notes,
                              "enabled": s.enabled, "configured": s.configured(), "probe": {k: pr[k] for k in ("at", "outcome", "rows", "elapsed_ms", "detail")} if pr else None,
                              "priority": self.priority.get(cap, []).index(name) + 1 if name in self.priority.get(cap, []) else None})
            out.append({"capability": cap, "description": desc, "sources": sorted(cells, key=lambda c: (c["priority"] is None, c["priority"] or 0)), "covered": bool(cells)})
        return out

    # ---- probing --------------------------------------------------------------------------------
    def probe(self, names: Optional[List[str]] = None, save_dir: Optional[str] = None) -> List[dict]:
        """Run a small fixed set of live requests per ENABLED source and record outcomes. Honest by construction: every outcome
        (ok / empty / blocked / timeout / unreachable / auth / http / parse) is stored with timestamp and environment."""
        env = json.dumps({"platform": platform.platform(), "python": platform.python_version()})
        season = current_season()
        results = []

        def rec(source, cap, outcome, rows=0, ms=0.0, url="", detail="", sample=None):
            self.store.execute("INSERT INTO source_probe (at, source, capability, outcome, rows, elapsed_ms, url, detail, sample_json, env) VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (_now(), source, cap, outcome, rows, ms, url, detail[:400], json.dumps(sample, default=str)[:4000] if sample is not None else None, env))
            results.append({"source": source, "capability": cap, "outcome": outcome, "rows": rows, "elapsed_ms": ms, "detail": detail[:200]})

        def run(name, cap, **params):
            s = self.sources[name]
            try:
                f = self.fetch_from(name, cap, **params)
                if save_dir and f.sample is not None:
                    import os
                    os.makedirs(save_dir, exist_ok=True)
                    with open(os.path.join(save_dir, f"{name}_{cap}.json"), "w", encoding="utf-8") as fh:
                        json.dump({"meta": f.meta, "sample": f.sample}, fh, default=str, indent=1)
                rec(name, cap, "ok" if f.records else "empty", len(f.records), f.meta.get("elapsed_ms", 0), f.meta.get("url", ""), "", f.sample)
                return f
            except SourceError as e:
                rec(name, cap, e.kind, 0, 0, "", e.detail)
                return None

        for name, s in self.sources.items():
            if names and name not in names:
                continue
            if not s.enabled:
                rec(name, "(all)", "not_enabled", detail="source disabled; nothing was requested")
                continue
            if not s.configured():
                rec(name, "(all)", "not_configured", detail=f"set {s.key_env}")
                continue
            if name == "NBA_STATS":
                sched = run(name, "schedule", season=season)
                run(name, "players_index", season=season)
                run(name, "player_game_logs", season=season, last_n=0)
                run(name, "player_game_logs_advanced", season=season)
                run(name, "team_game_logs", season=season)
                run(name, "player_season_advanced", season=season)
                run(name, "team_season_advanced", season=season)
                run(name, "lineup_combinations", season=season)
                run(name, "shot_locations", season=season)
                gid = next((r["game_id"] for r in (sched.records if sched else []) if r["status"] == "final"), None)
                if gid:
                    run(name, "box_score", game_id=gid)
                else:
                    rec(name, "box_score", "skipped", detail="no finished game id available from the schedule probe")
            elif name == "NBA_LIVE":
                sb = run(name, "live_scoreboard")
                g = next((r["game_id"] for r in (sb.records if sb else []) ), None)
                if g:
                    run(name, "live_box_score", game_id=g)
                    run(name, "starting_lineups", game_id=g)
                else:
                    rec(name, "live_box_score", "skipped", detail="no game on today's scoreboard")
            elif name == "NBA_INJURY_REPORT":
                run(name, "injuries")
            elif name == "BALLDONTLIE":
                run(name, "players_index", per_page=5)
                run(name, "schedule", per_page=5, **{"seasons[]": int(season[:4])})
                run(name, "injuries", per_page=5)
            elif name == "SPORTSDATAIO":
                run(name, "injuries")
                run(name, "starting_lineups", date=_now()[:10])
                run(name, "schedule", season=int(season[:4]) + 1)
        return results
