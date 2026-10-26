"""Interchangeable data-source layer.

A *source* (NBA stats, live NBA feed, BALLDONTLIE, SportsDataIO, a CSV, ...) declares which *capabilities* it can
serve (game logs, lineups, injuries, ...). The rest of the app asks the `SourceRouter` for a capability, never for a
vendor, so sources can be combined, re-ordered or replaced without touching the engine, models or UI.

Every `Fetched` result carries provenance (source, url, retrieved_at, status, latency, evidence level). Nothing is
fetched unless the owner has enabled the source (which records an explicit terms acknowledgement).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# capability id -> (short description, canonical record kind)
CAPABILITIES: Dict[str, str] = {
    "players_index": "All players with team, position, height, roster status",
    "schedule": "Season schedule / game ids / game status",
    "player_game_logs": "Per-player per-game box scores incl. minutes (historical)",
    "player_game_logs_advanced": "Per-player per-game advanced (usage, pace, ratings) - historical",
    "team_game_logs": "Per-team per-game results and box totals",
    "player_season_stats": "Season totals/per-game by player",
    "player_season_advanced": "Season advanced by player (usage, TS%, pace, ratings)",
    "team_season_advanced": "Season advanced by team (pace, ORtg/DRtg, four factors)",
    "lineup_combinations": "N-man lineup combination stats (on-court)",
    "shot_locations": "Shooting by zone, per player/team",
    "shot_chart": "Shot-level x/y locations",
    "box_score": "Single-game box score with starters and DNP reasons",
    "play_by_play": "Play-by-play for a finished game",
    "live_scoreboard": "Today's games with status",
    "live_box_score": "Live/near-tip box score with starters and inactive reasons",
    "injuries": "Current injury / availability reports",
    "starting_lineups": "Projected or confirmed starting lineups",
    "odds": "Betting odds / props",
    "projections": "Vendor-provided projections (for benchmarking only)",
}

EVIDENCE = ("probe_verified", "library_catalog", "documented", "unverified")
# probe_verified : a live request from a real environment returned usable data (recorded in source_probe)
# library_catalog: endpoint and columns exist in the nba_api source code (MIT); not proof it works today
# documented     : stated in the vendor's own documentation
# unverified     : inferred; response shape not confirmed


class SourceError(Exception):
    """kind: blocked | timeout | unreachable | http | auth | not_enabled | not_configured | parse | unsupported"""

    def __init__(self, kind: str, detail: str = "", status: Optional[int] = None):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail, self.status = kind, detail, status


@dataclass
class CapabilityInfo:
    capability: str
    endpoint: str
    tier: str = "free"           # free | key | paid:<tier name> | quote
    evidence: str = "unverified"
    notes: str = ""

    def to_dict(self) -> dict:
        return {"capability": self.capability, "endpoint": self.endpoint, "tier": self.tier, "evidence": self.evidence, "notes": self.notes}


@dataclass
class Fetched:
    capability: str
    source: str
    records: List[dict]
    meta: Dict[str, Any] = field(default_factory=dict)   # url, status, elapsed_ms, retrieved_at, rows, evidence, extra
    sample: Optional[Any] = None                          # trimmed raw payload for diagnostics

    def to_dict(self) -> dict:
        return {"capability": self.capability, "source": self.source, "n": len(self.records), "meta": self.meta}


class DataSource:
    """Base class. Subclasses implement `capabilities()` and `_fetch(cap, **params)`."""
    name = "SOURCE"
    kind = "stats"                 # stats | live | injuries | multi | file
    homepage = ""
    terms_url = ""
    terms_summary = ""             # shown in the enable dialog; must quote/point to evidence, never opine
    terms_status = "unreviewed"    # unreviewed | restriction_found | permissive_noted | owner_responsibility
    requires_key = False
    key_env = ""
    min_interval_s = 1.0

    def __init__(self, transport=None):
        from .transport import UrllibTransport
        self.transport = transport or UrllibTransport(min_interval_s=self.min_interval_s)
        self.enabled = False
        self.ack: Optional[dict] = None

    # ---- declared capability -----------------------------------------------------------------
    def capabilities(self) -> Dict[str, CapabilityInfo]:
        return {}

    def supports(self, cap: str) -> bool:
        return cap in self.capabilities()

    def key(self) -> Optional[str]:
        import os
        return os.environ.get(self.key_env) if self.key_env else None

    def configured(self) -> bool:
        return (not self.requires_key) or bool(self.key())

    # ---- fetch ---------------------------------------------------------------------------------
    def fetch(self, cap: str, **params) -> Fetched:
        if cap not in self.capabilities():
            raise SourceError("unsupported", f"{self.name} does not provide {cap}")
        if not self.enabled:
            raise SourceError("not_enabled", f"{self.name} is disabled; enable it (with terms acknowledgement) first")
        if not self.configured():
            raise SourceError("not_configured", f"{self.name} needs the {self.key_env} environment variable")
        f = self._fetch(cap, **params)
        f.meta.setdefault("evidence", self.capabilities()[cap].evidence)
        return f

    def _fetch(self, cap: str, **params) -> Fetched:  # pragma: no cover - abstract
        raise NotImplementedError

    def describe(self) -> dict:
        return {"name": self.name, "kind": self.kind, "enabled": self.enabled, "configured": self.configured(), "requires_key": self.requires_key,
                "key_env": self.key_env, "terms_url": self.terms_url, "terms_status": self.terms_status, "terms_summary": self.terms_summary,
                "homepage": self.homepage, "ack": self.ack, "capabilities": [c.to_dict() for c in self.capabilities().values()]}
