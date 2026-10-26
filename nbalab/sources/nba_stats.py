"""NBA.com stats (stats.nba.com) and NBA live-data (cdn.nba.com) sources.

Endpoint names, parameters and column lists come from `nba_stats_catalog.json`, generated offline from the MIT-licensed
`swar/nba_api` source (v1.12.0). Having an endpoint in the catalog is NOT proof that it works today (the nba_api
maintainers list endpoints that currently fail upstream; see KNOWN_BROKEN). `evidence` therefore starts at
`library_catalog` and only becomes `probe_verified` after a live probe from a real network (tools/probe_sources.py or the
Sources panel) succeeds.

Not used: nba_api itself (it needs pandas/requests; this app keeps dependencies minimal). The request headers mirror what
nba_api documents. NBA.com's Terms of Use restrict use of NBA statistics (see docs/LICENSING_AND_COMMERCIAL_CONCERNS.md);
the source stays disabled until the owner acknowledges that in the Sources panel.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .base import CapabilityInfo, DataSource, Fetched, SourceError

_CATALOG: Optional[dict] = None


def catalog() -> dict:
    global _CATALOG
    if _CATALOG is None:
        with open(os.path.join(os.path.dirname(__file__), "nba_stats_catalog.json"), encoding="utf-8") as f:
            _CATALOG = json.load(f)
    return _CATALOG


# From docs/nba_api/stats/known_upstream_issues.md in swar/nba_api (checked by its maintainers 2026-10-21/22).
KNOWN_BROKEN = {
    "draftboard": "HTTP 500 upstream", "playercareerbycollege": "HTTP 500 upstream", "teamgamestreakfinder": "HTTP 500 upstream",
    "teamhistoricalleaders": "HTTP 500 upstream", "winprobabilitypbp": "HTTP 500 upstream",
    "assistleaders": "removed upstream", "assisttracker": "removed upstream", "leagueleaders": "removed upstream", "playoffpicture": "removed upstream",
    "boxscoreadvancedv2": "removed upstream (use V3)", "boxscorefourfactorsv2": "removed upstream (use V3)", "boxscoremiscv2": "removed upstream (use V3)",
    "boxscorescoringv2": "removed upstream (use V3)", "boxscoreusagev2": "removed upstream (use V3)", "boxscoretraditionalv2": "no data since 2025-26 (use V3)",
    "playbyplay": "removed upstream (use V3)", "playbyplayv2": "removed upstream (use V3)", "scoreboardv2": "line score issues 2025-26 (use V3)",
}

STATS_HEADERS = {
    "Host": "stats.nba.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
}


# ---------------------------------------------------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------------------------------------------------
def parse_minutes(v) -> Optional[float]:
    """'34:12' | 'PT34M12.00S' | 34.2 | '' -> minutes as float (None when unknown)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    m = re.fullmatch(r"PT(\d+)M(\d+(?:\.\d+)?)S", s)
    if m:
        return int(m.group(1)) + float(m.group(2)) / 60.0
    m = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 60.0
    try:
        return float(s)
    except ValueError:
        return None


def result_sets(obj: dict) -> Dict[str, List[dict]]:
    """Legacy stats.nba.com payload ({resultSets:[{name,headers,rowSet}]}) -> {name: [row dicts]}."""
    rs = obj.get("resultSets", obj.get("resultSet"))
    if rs is None:
        raise SourceError("parse", "no resultSets in payload (endpoint may have been removed; keys: %s)" % ",".join(list(obj)[:6]))
    if isinstance(rs, dict):
        rs = [rs]
    out = {}
    for s in rs:
        hdr = s.get("headers")
        if hdr and isinstance(hdr[0], dict):      # multi-level header payloads (shot locations): keep raw
            out[s["name"]] = [{"_raw_headers": hdr, "_row": r} for r in s.get("rowSet", [])]
            continue
        out[s["name"]] = [dict(zip(hdr, r)) for r in s.get("rowSet", [])]
    return out


def _lower(row: dict) -> dict:
    return {str(k).lower(): v for k, v in row.items()}


def _date(s) -> Optional[str]:
    return str(s)[:10] if s else None


def _matchup(m: str):
    """'BOS vs. NYK' (home) / 'BOS @ NYK' (away) -> (team, opp, home)."""
    if not m:
        return None, None, None
    if "@" in m:
        a, b = [x.strip() for x in m.split("@")]
        return a, b, False
    a, b = [x.strip() for x in re.split(r"vs\.?", m)]
    return a, b, True


def norm_player_game_log(row: dict, source: str) -> dict:
    team, opp, home = _matchup(row.get("MATCHUP", ""))
    g = lambda k: row.get(k)
    return {"source": source, "season": g("SEASON_YEAR"), "game_id": str(g("GAME_ID")), "game_date": _date(g("GAME_DATE")), "player_id": g("PLAYER_ID"),
            "player_name": g("PLAYER_NAME"), "team_id": g("TEAM_ID"), "team_abbr": g("TEAM_ABBREVIATION") or team, "opp_abbr": opp, "home": home, "wl": g("WL"),
            "min": parse_minutes(g("MIN")), "pts": g("PTS"), "fgm": g("FGM"), "fga": g("FGA"), "fg3m": g("FG3M"), "fg3a": g("FG3A"), "ftm": g("FTM"), "fta": g("FTA"),
            "oreb": g("OREB"), "dreb": g("DREB"), "reb": g("REB"), "ast": g("AST"), "stl": g("STL"), "blk": g("BLK"), "tov": g("TOV"), "pf": g("PF"),
            "pm": g("PLUS_MINUS"), "started": None}


def norm_team_game_log(row: dict, source: str) -> dict:
    team, opp, home = _matchup(row.get("MATCHUP", ""))
    d = norm_player_game_log(row, source)
    d.pop("player_id", None), d.pop("player_name", None), d.pop("started", None)
    d["team_name"] = row.get("TEAM_NAME")
    return d


def norm_box_score_v3(obj: dict, source: str) -> List[dict]:
    """boxscoretraditionalv3 -> one row per player incl. starter flag and DNP comment."""
    b = obj.get("boxScoreTraditional") or {}
    gid = str(b.get("gameId", ""))
    rows = []
    for side in ("homeTeam", "awayTeam"):
        t = b.get(side) or {}
        starters = {int(p["personId"]) for p in (t.get("players") or []) if (p.get("position") or "").strip()}
        for p in t.get("players") or []:
            st = p.get("statistics") or {}
            pid = int(p["personId"])
            rows.append({"source": source, "game_id": gid, "team_id": t.get("teamId"), "team_abbr": t.get("teamTricode"), "home": side == "homeTeam",
                         "player_id": pid, "player_name": " ".join(x for x in (p.get("firstName"), p.get("familyName")) if x), "position": p.get("position") or "",
                         "started": pid in starters, "comment": p.get("comment") or "", "min": parse_minutes(st.get("minutes")),
                         "pts": st.get("points"), "fgm": st.get("fieldGoalsMade"), "fga": st.get("fieldGoalsAttempted"), "fg3m": st.get("threePointersMade"),
                         "fg3a": st.get("threePointersAttempted"), "ftm": st.get("freeThrowsMade"), "fta": st.get("freeThrowsAttempted"),
                         "oreb": st.get("reboundsOffensive"), "dreb": st.get("reboundsDefensive"), "reb": st.get("reboundsTotal"), "ast": st.get("assists"),
                         "stl": st.get("steals"), "blk": st.get("blocks"), "tov": st.get("turnovers"), "pf": st.get("foulsPersonal"), "pm": st.get("plusMinusPoints")})
    return rows


def norm_schedule_v2(obj: dict, source: str) -> List[dict]:
    ls = obj.get("leagueSchedule") or {}
    out = []
    for gd in ls.get("gameDates") or []:
        for g in gd.get("games") or []:
            h, a = g.get("homeTeam") or {}, g.get("awayTeam") or {}
            out.append({"source": source, "season": ls.get("seasonYear"), "game_id": str(g.get("gameId")), "game_date": _date(g.get("gameDateEst") or g.get("gameDateUTC") or gd.get("gameDate")),
                        "game_time_utc": g.get("gameDateTimeUTC"), "status": {1: "scheduled", 2: "live", 3: "final"}.get(g.get("gameStatus"), str(g.get("gameStatus"))),
                        "home_team_id": h.get("teamId"), "home_abbr": h.get("teamTricode"), "home_name": " ".join(x for x in (h.get("teamCity"), h.get("teamName")) if x),
                        "away_team_id": a.get("teamId"), "away_abbr": a.get("teamTricode"), "away_name": " ".join(x for x in (a.get("teamCity"), a.get("teamName")) if x),
                        "home_score": h.get("score"), "away_score": a.get("score"), "label": g.get("gameLabel") or ""})
    return out


def norm_players_index(row: dict, source: str) -> dict:
    ht = row.get("HEIGHT") or ""
    m = re.fullmatch(r"(\d+)-(\d+)", str(ht))
    return {"source": source, "player_id": row.get("PERSON_ID"), "player_name": f"{row.get('PLAYER_FIRST_NAME','')} {row.get('PLAYER_LAST_NAME','')}".strip(),
            "team_id": row.get("TEAM_ID"), "team_abbr": row.get("TEAM_ABBREVIATION"), "position": row.get("POSITION") or "", "height_in": int(m.group(1)) * 12 + int(m.group(2)) if m else None,
            "roster_status": row.get("ROSTER_STATUS"), "from_year": row.get("FROM_YEAR"), "to_year": row.get("TO_YEAR"), "defunct": bool(row.get("IS_DEFUNCT"))}


def norm_shot_locations(rows: List[dict], source: str) -> List[dict]:
    """leaguedashplayershotlocations has a two-level header: 5 id columns then (FGM, FGA, FG_PCT) per zone."""
    out = []
    if not rows or "_raw_headers" not in rows[0]:
        return [_lower(r) | {"source": source} for r in rows]
    hdr = rows[0]["_raw_headers"]
    zones = next((h["columnNames"] for h in hdr if h.get("name") == "SHOT_CATEGORY"), [])
    for r in rows:
        v = r["_row"]
        d = {"source": source, "player_id": v[0], "player_name": v[1], "team_id": v[2], "team_abbr": v[3], "age": v[4]}
        for i, z in enumerate(zones):
            base = 5 + 3 * i
            key = re.sub(r"[^a-z0-9]+", "_", z.lower()).strip("_")
            d[key + "_fgm"], d[key + "_fga"], d[key + "_fg_pct"] = v[base], v[base + 1], v[base + 2]
        out.append(d)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# stats.nba.com
# ---------------------------------------------------------------------------------------------------------------------
# capability -> (endpoint, parameter overrides, result-set name or None for custom, note)
STATS_MAP: Dict[str, tuple] = {
    "players_index": ("playerindex", {}, "PlayerIndex", "Player bio and roster status, one row per player"),
    "schedule": ("scheduleleaguev2", {}, None, "Whole-season schedule incl. game ids and status"),
    "player_game_logs": ("playergamelogs", {"MeasureType": "Base", "PerMode": "Totals"}, "PlayerGameLogs", "All players' games for a season in one request; includes MIN"),
    "player_game_logs_advanced": ("playergamelogs", {"MeasureType": "Advanced", "PerMode": "Totals"}, "PlayerGameLogs", "Per-game usage, pace, ratings (MeasureType=Advanced)"),
    "team_game_logs": ("teamgamelogs", {"MeasureType": "Base", "PerMode": "Totals"}, "TeamGameLogs", "All teams' games, scores and result"),
    "player_season_stats": ("leaguedashplayerstats", {"MeasureType": "Base", "PerMode": "Totals"}, "LeagueDashPlayerStats", "Season totals; filterable by date, location, outcome, last N"),
    "player_season_advanced": ("leaguedashplayerstats", {"MeasureType": "Advanced", "PerMode": "PerGame"}, "LeagueDashPlayerStats", "Usage%, TS%, PIE, pace, ratings per player"),
    "team_season_advanced": ("leaguedashteamstats", {"MeasureType": "Advanced", "PerMode": "PerGame"}, "LeagueDashTeamStats", "Pace, ORtg/DRtg/NetRtg per team"),
    "lineup_combinations": ("leaguedashlineups", {"GroupQuantity": 5, "MeasureType": "Advanced", "PerMode": "Totals"}, "Lineups", "On-court N-man lineup stats (GroupQuantity 2-5)"),
    "shot_locations": ("leaguedashplayershotlocations", {"DistanceRange": "By Zone"}, "ShotLocations", "FGM/FGA by court zone per player"),
    "shot_chart": ("shotchartdetail", {"ContextMeasure": "FGA", "TeamID": 0}, "Shot_Chart_Detail", "Shot-level LOC_X/LOC_Y; pass player_id"),
    "box_score": ("boxscoretraditionalv3", {}, None, "Finished-game box with starters and DNP comments; pass game_id as a string"),
    "play_by_play": ("playbyplayv3", {}, None, "Finished-game play-by-play; pass game_id"),
}
# lab-friendly parameter names -> stats.nba.com names
PARAM_ALIASES = {"season": "Season", "season_type": "SeasonType", "game_id": "GameID", "player_id": "PlayerID", "team_id": "TeamID", "date_from": "DateFrom",
                 "date_to": "DateTo", "last_n": "LastNGames", "per_mode": "PerMode", "measure_type": "MeasureType", "group_quantity": "GroupQuantity"}


class NbaStatsSource(DataSource):
    name = "NBA_STATS"
    kind = "stats"
    homepage = "https://www.nba.com/stats"
    terms_url = "https://www.nba.com/termsofuse"
    terms_status = "restriction_found"
    terms_summary = ("NBA.com Terms of Use (page dated 2026-07-13), Section 9 'NBA Statistics': use is limited to legitimate news reporting or private, "
                     "non-commercial purposes, and statistics may not be used 'in connection with any fantasy game or other commercial product or service' "
                     "or with 'any website, product, or service that features a database' of 'comprehensive, regularly updated statistics' without the "
                     "Operator's prior consent. Read the page yourself before enabling; this app does not decide what you may do.")
    min_interval_s = 1.2

    def capabilities(self) -> Dict[str, CapabilityInfo]:
        cat = catalog()["endpoints"]
        out = {}
        for cap, (ep, _ov, _ds, note) in STATS_MAP.items():
            if ep in cat:
                broken = KNOWN_BROKEN.get(ep)
                out[cap] = CapabilityInfo(cap, ep, "free", "library_catalog", note + (f" | KNOWN ISSUE: {broken}" if broken else ""))
        return out

    def build_request(self, cap: str, **params) -> tuple:
        ep, overrides, _ds, _n = STATS_MAP[cap]
        defaults = dict(catalog()["endpoints"][ep]["parameters"])
        p = {k: ("" if v is None else v) for k, v in defaults.items()}
        p.update(overrides)
        for k, v in params.items():
            key = PARAM_ALIASES.get(k, k)
            if key not in p and k not in p:
                raise SourceError("unsupported", f"{ep} has no parameter '{k}' (valid: {', '.join(sorted(defaults))})")
            p[key if key in p else k] = v
        if "GameID" in p and p["GameID"] != "":
            p["GameID"] = str(p["GameID"])
        return f"https://stats.nba.com/stats/{ep}", p

    def _fetch(self, cap: str, **params) -> Fetched:
        url, p = self.build_request(cap, **params)
        res = self.transport.get(url, p, headers=STATS_HEADERS, timeout=30.0)
        obj = res.json()
        ep, _ov, ds, _n = STATS_MAP[cap]
        recs = self.normalize(cap, obj)
        meta = {"url": res.url, "status": res.status, "elapsed_ms": round(res.elapsed_ms), "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endpoint": ep, "rows": len(recs)}
        return Fetched(cap, self.name, recs, meta, sample=recs[:2])

    def normalize(self, cap: str, obj: dict) -> List[dict]:
        src = self.name
        if cap == "schedule":
            return norm_schedule_v2(obj, src)
        if cap == "box_score":
            return norm_box_score_v3(obj, src)
        if cap == "play_by_play":
            acts = (obj.get("game") or {}).get("actions") or []
            return [_lower(a) | {"source": src} for a in acts]
        sets = result_sets(obj)
        ds = STATS_MAP[cap][2]
        rows = sets.get(ds)
        if rows is None:
            raise SourceError("parse", f"result set {ds!r} missing (got {list(sets)})")
        if cap == "player_game_logs":
            return [norm_player_game_log(r, src) for r in rows]
        if cap == "team_game_logs":
            return [norm_team_game_log(r, src) for r in rows]
        if cap == "players_index":
            return [norm_players_index(r, src) for r in rows]
        if cap == "shot_locations":
            return norm_shot_locations(rows, src)
        return [_lower(r) | {"source": src} for r in rows]


# ---------------------------------------------------------------------------------------------------------------------
# cdn.nba.com live data (scoreboard, box score with starters / inactive reasons)
# ---------------------------------------------------------------------------------------------------------------------
LIVE_BASE = "https://cdn.nba.com/static/json/liveData"


class NbaLiveSource(DataSource):
    name = "NBA_LIVE"
    kind = "live"
    homepage = "https://www.nba.com/games"
    terms_url = "https://www.nba.com/termsofuse"
    terms_status = "restriction_found"
    terms_summary = NbaStatsSource.terms_summary
    min_interval_s = 1.0

    def capabilities(self) -> Dict[str, CapabilityInfo]:
        return {
            "live_scoreboard": CapabilityInfo("live_scoreboard", "liveData/scoreboard/todaysScoreboard_00.json", "free", "library_catalog", "Today's games, status 1/2/3"),
            "live_box_score": CapabilityInfo("live_box_score", "liveData/boxscore/boxscore_{gameId}.json", "free", "library_catalog",
                                             "Per-player 'starter' flag, ACTIVE/INACTIVE status and notPlayingReason; when it is populated before tip is UNVERIFIED"),
            "starting_lineups": CapabilityInfo("starting_lineups", "liveData/boxscore (starter flag)", "free", "unverified", "Derived from the live box score once starters are posted"),
            "injuries": CapabilityInfo("injuries", "liveData/boxscore (INACTIVE + notPlayingReason)", "free", "unverified", "Game-day inactives only; not a full injury report"),
        }

    def _get(self, path: str):
        res = self.transport.get(f"{LIVE_BASE}/{path}", None, headers={"Referer": "https://www.nba.com/", "Origin": "https://www.nba.com"})
        return res, res.json()

    def _fetch(self, cap: str, **params) -> Fetched:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if cap == "live_scoreboard":
            res, obj = self._get("scoreboard/todaysScoreboard_00.json")
            recs = self.norm_scoreboard(obj)
        else:
            gid = params.get("game_id")
            if not gid:
                raise SourceError("unsupported", "game_id is required")
            res, obj = self._get(f"boxscore/boxscore_{gid}.json")
            recs = self.norm_live_box(obj)
            if cap == "starting_lineups":
                recs = [r for r in recs if r["starter"]]
            elif cap == "injuries":
                recs = [r for r in recs if r["status"] == "INACTIVE"]
        return Fetched(cap, self.name, recs, {"url": res.url, "status": res.status, "elapsed_ms": round(res.elapsed_ms), "retrieved_at": now, "rows": len(recs)}, sample=recs[:2])

    @staticmethod
    def norm_scoreboard(obj: dict) -> List[dict]:
        sb = obj.get("scoreboard") or {}
        out = []
        for g in sb.get("games") or []:
            h, a = g.get("homeTeam") or {}, g.get("awayTeam") or {}
            out.append({"source": "NBA_LIVE", "game_id": str(g.get("gameId")), "game_date": sb.get("gameDate"), "status_code": g.get("gameStatus"),
                        "status": {1: "scheduled", 2: "live", 3: "final"}.get(g.get("gameStatus")), "status_text": g.get("gameStatusText"), "tip_utc": g.get("gameTimeUTC"),
                        "home_team_id": h.get("teamId"), "home_abbr": h.get("teamTricode"), "home_score": h.get("score"),
                        "away_team_id": a.get("teamId"), "away_abbr": a.get("teamTricode"), "away_score": a.get("score")})
        return out

    @staticmethod
    def norm_live_box(obj: dict) -> List[dict]:
        g = obj.get("game") or {}
        out = []
        for side in ("homeTeam", "awayTeam"):
            t = g.get(side) or {}
            for p in t.get("players") or []:
                st = p.get("statistics") or {}
                out.append({"source": "NBA_LIVE", "game_id": str(g.get("gameId")), "team_id": t.get("teamId"), "team_abbr": t.get("teamTricode"), "home": side == "homeTeam",
                            "player_id": p.get("personId"), "player_name": p.get("name"), "position": p.get("position") or "", "starter": str(p.get("starter")) == "1",
                            "status": p.get("status"), "reason": p.get("notPlayingReason") or "", "reason_detail": p.get("notPlayingDescription") or "",
                            "min": parse_minutes(st.get("minutes")), "pts": st.get("points"), "game_status_text": g.get("gameStatusText")})
        return out
