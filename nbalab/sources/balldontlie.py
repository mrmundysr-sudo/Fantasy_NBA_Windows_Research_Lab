"""BALLDONTLIE (api.balldontlie.io). Key from env BALLDONTLIE_API_KEY; tier from env BALLDONTLIE_TIER (free|all-star|goat).

Endpoints and tier gating are from the vendor docs (nba.balldontlie.io, fetched 2026-10-25). Response *shapes* for injuries and
box/advanced rows were only partly visible in those docs, so normalizers are tolerant and the evidence level stays 'documented'
or 'unverified' until a keyed probe succeeds. Free tier is 5 requests/minute: the transport spaces calls accordingly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from .base import CapabilityInfo, DataSource, Fetched, SourceError
from .nba_stats import parse_minutes

BASE = "https://api.balldontlie.io"
TIERS = {"free": 12.5, "all-star": 1.05, "goat": 0.11, "all-access": 0.11}
TIER_RANK = {"free": 0, "all-star": 1, "goat": 2, "all-access": 3}

# capability -> (path, minimum tier, evidence, notes)
PATHS = {
    "players_index": ("/v1/players", "free", "documented", "Players (cursor paginated, per_page<=100)"),
    "schedule": ("/v1/games", "free", "documented", "Games by season/date"),
    "player_game_logs": ("/v1/stats", "all-star", "documented", "Per-player per-game stats; min is a string"),
    "injuries": ("/v1/player_injuries", "all-star", "documented", "Current injury list; field names partly unverified"),
    "starting_lineups": ("/v1/lineups", "goat", "documented", "Requires game_ids[]; data from 2025 season; available once the game begins (so NOT pre-game)"),
    "player_game_logs_advanced": ("/nba/v2/stats/advanced", "goat", "documented", "Usage, pace, ratings, hustle, tracking; period=0 for full game"),
    "box_score": ("/v1/box_scores", "goat", "documented", "Box scores (also /v1/box_scores/live)"),
    "player_season_stats": ("/v1/season_averages/general", "goat", "documented", "Docs are inconsistent about /v1 vs /nba/v1 for season averages"),
    "play_by_play": ("/v1/plays", "goat", "documented", "Plays; data from 2025 season"),
    "odds": ("/v1/odds", "goat", "documented", "Betting odds"),
}
STATUS_MAP = {"out": "out", "out for season": "out", "day-to-day": "questionable", "questionable": "questionable", "doubtful": "doubtful", "probable": "probable", "available": "available"}


class BallDontLieSource(DataSource):
    name = "BALLDONTLIE"
    kind = "multi"
    homepage = "https://www.balldontlie.io"
    terms_url = "https://www.balldontlie.io/terms"
    terms_status = "unreviewed"
    terms_summary = "Not yet read. Pricing (per vendor page, 2026-10-25): Free $0 / 5 req/min; ALL-STAR $9.99 / 60; GOAT $39.99 / 600 (per sport). Review the vendor terms before any non-private use."
    requires_key = True
    key_env = "BALLDONTLIE_API_KEY"

    def __init__(self, transport=None, tier: str = ""):
        import os
        self.tier = (tier or os.environ.get("BALLDONTLIE_TIER", "free")).lower()
        self.min_interval_s = TIERS.get(self.tier, 12.5)
        super().__init__(transport)

    def capabilities(self) -> Dict[str, CapabilityInfo]:
        out = {}
        for cap, (path, tier, ev, note) in PATHS.items():
            out[cap] = CapabilityInfo(cap, path, f"paid:{tier}" if tier != "free" else "key (free tier)", ev, note + ("" if TIER_RANK.get(self.tier, 0) >= TIER_RANK[tier] else f" | needs {tier} tier, configured tier is {self.tier}"))
        return out

    def _headers(self):
        return {"Authorization": self.key() or ""}

    def _fetch(self, cap: str, **params) -> Fetched:
        path, tier, _ev, _n = PATHS[cap]
        if TIER_RANK.get(self.tier, 0) < TIER_RANK[tier]:
            raise SourceError("auth", f"{cap} needs the {tier} tier (configured: {self.tier})")
        q = {"per_page": 100}
        for k, v in params.items():
            if k in ("max_pages",):
                continue
            q[k] = v
        recs, pages, cursor, last = [], 0, None, None
        max_pages = int(params.get("max_pages", 1))
        while pages < max_pages:
            if cursor:
                q["cursor"] = cursor
            last = self.transport.get(BASE + path, q, headers=self._headers(), timeout=30.0)
            obj = last.json()
            data = obj.get("data")
            if data is None:
                raise SourceError("parse", "no 'data' in response")
            recs.extend(data if isinstance(data, list) else [data])
            cursor = (obj.get("meta") or {}).get("next_cursor")
            pages += 1
            if not cursor:
                break
        norm = [self.normalize(cap, r) for r in recs]
        meta = {"url": last.url, "status": last.status, "elapsed_ms": round(last.elapsed_ms), "pages": pages, "more": bool(cursor), "rows": len(norm),
                "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        return Fetched(cap, self.name, norm, meta, sample=norm[:2])

    def normalize(self, cap: str, r: dict) -> dict:
        s = self.name
        pl, tm = r.get("player") or {}, r.get("team") or {}
        name = " ".join(x for x in (pl.get("first_name"), pl.get("last_name")) if x)
        if cap == "injuries":
            st = str(r.get("status", "")).strip()
            return {"source": s, "player_id": pl.get("id"), "player_name": name, "team_abbr": (pl.get("team") or tm or {}).get("abbreviation"), "status_raw": st,
                    "status": STATUS_MAP.get(st.lower(), st.lower()), "detail": r.get("description") or "", "return_date": r.get("return_date"), "updated_at": r.get("updated_at")}
        if cap == "starting_lineups":
            return {"source": s, "game_id": r.get("game_id"), "player_id": pl.get("id"), "player_name": name, "team_abbr": tm.get("abbreviation"),
                    "starter": bool(r.get("starter")), "position": r.get("position") or pl.get("position")}
        if cap == "player_game_logs":
            g = r.get("game") or {}
            return {"source": s, "game_id": str(g.get("id", r.get("game_id", ""))), "game_date": str(g.get("date", ""))[:10], "season": g.get("season"), "player_id": pl.get("id"),
                    "player_name": name, "team_abbr": tm.get("abbreviation"), "min": parse_minutes(r.get("min")), "pts": r.get("pts"), "fgm": r.get("fgm"), "fga": r.get("fga"),
                    "fg3m": r.get("fg3m"), "fg3a": r.get("fg3a"), "ftm": r.get("ftm"), "fta": r.get("fta"), "oreb": r.get("oreb"), "dreb": r.get("dreb"), "reb": r.get("reb"),
                    "ast": r.get("ast"), "stl": r.get("stl"), "blk": r.get("blk"), "tov": r.get("turnover", r.get("tov")), "pf": r.get("pf"), "started": None}
        if cap == "players_index":
            return {"source": s, "player_id": r.get("id"), "player_name": f"{r.get('first_name','')} {r.get('last_name','')}".strip(), "position": r.get("position") or "",
                    "team_abbr": (r.get("team") or {}).get("abbreviation"), "height": r.get("height"), "jersey": r.get("jersey_number")}
        if cap == "schedule":
            return {"source": s, "game_id": str(r.get("id")), "game_date": str(r.get("date", ""))[:10], "season": r.get("season"), "status": r.get("status"),
                    "home_abbr": (r.get("home_team") or {}).get("abbreviation"), "away_abbr": (r.get("visitor_team") or {}).get("abbreviation"),
                    "home_score": r.get("home_team_score"), "away_score": r.get("visitor_team_score")}
        return dict(r, source=s)
