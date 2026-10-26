"""Import real NBA data (through any enabled source) into a lab database that is SEPARATE from the synthetic fixture.

What is imported: players (index), teams, schedule with scores/status, player game logs (minutes and box counting stats).
What is NOT available from game logs and is stored as missing (NULL -> NaN, never zero): tracking columns (touches, drives,
potential assists, contested shots), coaches (one 'Unknown' placeholder), overtime flag (0), on/off.
`started` is INFERRED (top five by minutes per team-game) until `backfill_starters` has fetched real box scores; the dataset
meta records `started_inferred`. Positions G/F/C hybrids are mapped to the five-position scheme for priors only (approximation).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from ..store import Store
from .base import SourceError

POS_MAP = {"G": "PG", "G-F": "SG", "F-G": "SF", "F": "PF", "F-C": "PF", "C-F": "C", "C": "C", "PG": "PG", "SG": "SG", "SF": "SF", "PF": "PF", "": "SF"}


def _gid(s) -> int:
    return int(re.sub(r"\D", "", str(s)) or 0)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def import_real(router, store: Store, seasons: List[str], progress: Optional[Callable[[str], None]] = None, label: str = "NBA_STATS_IMPORT") -> dict:
    say = progress or (lambda m: None)
    teams: Dict[int, dict] = {}
    players: Dict[int, dict] = {}
    games: Dict[int, tuple] = {}
    box: List[tuple] = []
    info = {"seasons": seasons, "sources": {}, "skipped": {}}
    for season in seasons:
        say(f"{season}: players index")
        pidx = router.fetch("players_index", season=season)
        info["sources"]["players_index"] = pidx.source
        for r in pidx.records:
            if r.get("team_id"):
                teams.setdefault(int(r["team_id"]), {"abbr": r.get("team_abbr") or "", "name": r.get("team_abbr") or ""})
            players[int(r["player_id"])] = {"name": r["player_name"], "team_id": int(r["team_id"]) if r.get("team_id") else 0, "position": POS_MAP.get(r.get("position") or "", "SF"),
                                            "height_in": r.get("height_in")}
        say(f"{season}: schedule")
        sch = router.fetch("schedule", season=season)
        info["sources"]["schedule"] = sch.source
        for g in sch.records:
            if not g.get("home_team_id") or not g.get("away_team_id") or not g.get("game_date"):
                continue
            for side in ("home", "away"):
                t = int(g[f"{side}_team_id"])
                teams.setdefault(t, {"abbr": g.get(f"{side}_abbr") or "", "name": g.get(f"{side}_name") or ""})
                if g.get(f"{side}_name"):
                    teams[t]["name"] = g[f"{side}_name"]
                if g.get(f"{side}_abbr"):
                    teams[t]["abbr"] = g[f"{side}_abbr"]
            games[_gid(g["game_id"])] = (season, g["game_date"], int(g["home_team_id"]), int(g["away_team_id"]), g["status"], g.get("home_score"), g.get("away_score"))
        say(f"{season}: player game logs")
        logs = router.fetch("player_game_logs", season=season)
        info["sources"]["player_game_logs"] = logs.source
        by_team_game: Dict[tuple, list] = {}
        for r in logs.records:
            gid = _gid(r["game_id"])
            if gid not in games or r.get("min") is None:
                info["skipped"]["log_without_game_or_minutes"] = info["skipped"].get("log_without_game_or_minutes", 0) + 1
                continue
            pid = int(r["player_id"])
            tid = int(r["team_id"]) if r.get("team_id") else 0
            players.setdefault(pid, {"name": r.get("player_name") or str(pid), "team_id": tid, "position": "SF", "height_in": None})
            by_team_game.setdefault((gid, tid), []).append(r)
        for (gid, tid), rows in by_team_game.items():
            top5 = {int(r["player_id"]) for r in sorted(rows, key=lambda r: -(r["min"] or 0))[:5]}
            for r in rows:
                box.append((gid, int(r["player_id"]), tid, 1 if int(r["player_id"]) in top5 else 0, r["min"], r.get("pts"), r.get("fgm"), r.get("fga"), r.get("fg3m"), r.get("fg3a"),
                            r.get("ftm"), r.get("fta"), r.get("oreb"), r.get("dreb"), r.get("reb"), r.get("ast"), r.get("stl"), r.get("blk"), r.get("tov"), r.get("pf"), r.get("pm"),
                            None, None, None, None))
    # keep a final game only if both teams have box rows (otherwise it would corrupt team totals)
    have = {(b[0], b[2]) for b in box}
    game_rows, dropped = [], 0
    for gid, (season, d, h, a, status, hs, as_) in games.items():
        if status == "final" and not ((gid, h) in have and (gid, a) in have and hs is not None and as_ is not None):
            dropped += 1
            continue
        if status == "live":
            status = "scheduled"
        game_rows.append((gid, season, d, h, a, 0, 0, status, hs, as_, 0, label))
    keep = {g[0] for g in game_rows if g[7] == "final"}
    box = [b for b in box if b[0] in keep]
    info["skipped"]["final_games_without_complete_box"] = dropped
    used_teams = {t for g in game_rows for t in (g[3], g[4])}
    store.insert_many("coaches", ["coach_id", "name", "data_label"], [(0, "Unknown (not provided by source)", label)])
    store.insert_many("teams", ["team_id", "abbr", "name", "city", "conference", "lat", "lon", "data_label"],
                      [(t, d["abbr"], d["name"], "", "", None, None, label) for t, d in teams.items() if t in used_teams])
    store.insert_many("players", ["player_id", "name", "team_id", "position", "age", "height_in", "data_label"],
                      [(p, d["name"], d["team_id"], d["position"], None, d.get("height_in"), label) for p, d in players.items()])
    store.insert_many("games", ["game_id", "season", "game_date", "home_team_id", "away_team_id", "home_coach_id", "away_coach_id", "status", "home_score", "away_score", "ot", "data_label"], game_rows)
    store.insert_many("box", ["game_id", "player_id", "team_id", "started", "min", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf", "pm",
                              "touches", "drives", "pot_ast", "contested"], box)
    store.set_meta("data_label", label)
    store.set_meta("synthetic", False)
    store.set_meta("dataset_note", "Imported from NBA data sources via nbalab.sources. Tracking columns are missing; starters are inferred from minutes; coaches unknown. Verify against the source before relying on it.")
    store.set_meta("import_info", {**info, "imported_at": _now(), "games": len(game_rows), "box_rows": len(box), "started_inferred": True})
    say(f"imported {len(game_rows)} games, {len(box)} player-game rows")
    return {"games": len(game_rows), "box_rows": len(box), "players": len(players), "teams": len(used_teams), **info}
