"""In-memory columnar dataset (numpy) built from the store.

Rows are player-games (`pg`) and team-games (`tg`). Team and opponent context columns are
joined onto every player-game row so any metric formula can be evaluated on window sums.
Possessions are estimated per game as the mean of both teams' (FGA + 0.44*FTA - OREB + TOV);
that estimate is labelled as such everywhere it is used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import numpy as np

from .schema import BOX_COLS, CTX_COLS
from .store import Store


@dataclass
class Dataset:
    pg: Dict[str, np.ndarray]
    tg: Dict[str, np.ndarray]
    players: Dict[int, dict]
    teams: Dict[int, dict]
    coaches: Dict[int, dict]
    games: Dict[int, dict]
    seasons: List[str]
    meta: dict
    pg_gt: np.ndarray = field(default=None)       # index of the team-game row for each player-game row
    tg_index: Dict[tuple, int] = field(default_factory=dict)
    lineup_ids: Dict[tuple, int] = field(default_factory=dict)
    lineup_names: Dict[int, str] = field(default_factory=dict)
    played_cache: Dict[int, np.ndarray] = field(default_factory=dict)

    @property
    def synthetic(self) -> bool:
        return bool(self.meta.get("synthetic"))

    def label(self) -> str:
        return self.meta.get("data_label", "UNKNOWN")


def _ord(d: str) -> int:
    return date.fromisoformat(d).toordinal()


def build(store: Store) -> Dataset:
    meta = {r["key"]: __import__("json").loads(r["value"]) for r in store.query("SELECT key,value FROM meta")}
    teams = {r["team_id"]: dict(r) for r in store.query("SELECT * FROM teams")}
    coaches = {r["coach_id"]: dict(r) for r in store.query("SELECT * FROM coaches")}
    players = {r["player_id"]: dict(r) for r in store.query("SELECT * FROM players")}
    games = {r["game_id"]: dict(r) for r in store.query("SELECT * FROM games")}
    final_ids = sorted(g for g, r in games.items() if r["status"] == "final")
    seasons = sorted({games[g]["season"] for g in games})
    season_idx = {s: i for i, s in enumerate(seasons)}

    rows = store.query("SELECT * FROM box ORDER BY game_id, team_id, player_id")
    n = len(rows)
    pg: Dict[str, np.ndarray] = {}
    pg["game_id"] = np.fromiter((r["game_id"] for r in rows), dtype=np.int64, count=n)
    pg["player_id"] = np.fromiter((r["player_id"] for r in rows), dtype=np.int64, count=n)
    pg["team_id"] = np.fromiter((r["team_id"] for r in rows), dtype=np.int64, count=n)
    pg["started"] = np.fromiter((0 if r["started"] is None else r["started"] for r in rows), dtype=np.int64, count=n)
    for c in BOX_COLS:
        pg[c] = np.fromiter((np.nan if r[c] is None else r[c] for r in rows), dtype=float, count=n)

    g_arr = pg["game_id"]
    ginfo = games
    pg["date"] = np.fromiter((_ord(ginfo[g]["game_date"]) for g in g_arr), dtype=np.int64, count=n)
    pg["season"] = np.fromiter((season_idx[ginfo[g]["season"]] for g in g_arr), dtype=np.int64, count=n)
    pg["home"] = np.fromiter((1 if ginfo[g]["home_team_id"] == t else 0 for g, t in zip(g_arr, pg["team_id"])), dtype=np.int64, count=n)
    pg["opp_team_id"] = np.fromiter((ginfo[g]["away_team_id"] if ginfo[g]["home_team_id"] == t else ginfo[g]["home_team_id"]
                                     for g, t in zip(g_arr, pg["team_id"])), dtype=np.int64, count=n)
    pg["coach_id"] = np.fromiter((ginfo[g]["home_coach_id"] if ginfo[g]["home_team_id"] == t else ginfo[g]["away_coach_id"]
                                  for g, t in zip(g_arr, pg["team_id"])), dtype=np.int64, count=n)

    # ---- team-game table: sums of player rows -----------------------------------------------
    key_to_row: Dict[tuple, int] = {}
    keys = []
    for g in final_ids:
        gi = ginfo[g]
        for t in (gi["home_team_id"], gi["away_team_id"]):
            key_to_row[(g, t)] = len(keys)
            keys.append((g, t))
    m = len(keys)
    pg_gt = np.fromiter((key_to_row[(g, t)] for g, t in zip(g_arr, pg["team_id"])), dtype=np.int64, count=n)
    tg: Dict[str, np.ndarray] = {"game_id": np.array([k[0] for k in keys], dtype=np.int64), "team_id": np.array([k[1] for k in keys], dtype=np.int64)}
    for c in BOX_COLS:
        tg[c] = np.bincount(pg_gt, weights=pg[c], minlength=m)
    tg["pm"] = np.array([((ginfo[g]["home_score"] - ginfo[g]["away_score"]) if ginfo[g]["home_team_id"] == t else (ginfo[g]["away_score"] - ginfo[g]["home_score"]))
                         for g, t in keys], dtype=float)
    opp_row = np.fromiter((key_to_row[(g, ginfo[g]["away_team_id"] if ginfo[g]["home_team_id"] == t else ginfo[g]["home_team_id"])] for g, t in keys), dtype=np.int64, count=m)
    own_poss = tg["fga"] + 0.44 * tg["fta"] - tg["oreb"] + tg["tov"]
    game_poss = 0.5 * (own_poss + own_poss[opp_row])
    tg["tm_min"], tg["tm_fga"], tg["tm_fta"], tg["tm_tov"], tg["tm_fgm"] = tg["min"].copy(), tg["fga"].copy(), tg["fta"].copy(), tg["tov"].copy(), tg["fgm"].copy()
    tg["tm_reb"], tg["tm_oreb"], tg["tm_dreb"], tg["tm_pts"] = tg["reb"].copy(), tg["oreb"].copy(), tg["dreb"].copy(), tg["pts"].copy()
    tg["tm_poss"] = game_poss
    tg["opp_poss"] = game_poss.copy()
    tg["opp_pts"] = tg["pts"][opp_row]
    tg["opp_reb"], tg["opp_oreb"], tg["opp_dreb"] = tg["reb"][opp_row], tg["oreb"][opp_row], tg["dreb"][opp_row]
    tg["opp_fga"], tg["opp_fg3a"] = tg["fga"][opp_row], tg["fg3a"][opp_row]
    tg["gp"] = np.ones(m)
    tg["gs"] = np.zeros(m)
    tg["wins"] = (tg["pm"] > 0).astype(float)
    tg["date"] = np.array([_ord(ginfo[g]["game_date"]) for g, _ in keys], dtype=np.int64)
    tg["season"] = np.array([season_idx[ginfo[g]["season"]] for g, _ in keys], dtype=np.int64)
    tg["home"] = np.array([1 if ginfo[g]["home_team_id"] == t else 0 for g, t in keys], dtype=np.int64)
    tg["opp_team_id"] = np.array([keys[opp_row[i]][1] for i in range(m)], dtype=np.int64)
    tg["coach_id"] = np.array([ginfo[g]["home_coach_id"] if ginfo[g]["home_team_id"] == t else ginfo[g]["away_coach_id"] for g, t in keys], dtype=np.int64)
    tg["ot"] = np.array([ginfo[g]["ot"] for g, _ in keys], dtype=np.int64)
    # rest days and back-to-back from each team's schedule (games in the store only)
    rest = np.full(m, 7, dtype=np.int64)
    order = np.lexsort((tg["date"], tg["team_id"]))
    last_t, last_d = -1, 0
    for idx in order:
        t, d = int(tg["team_id"][idx]), int(tg["date"][idx])
        rest[idx] = (d - last_d - 1) if t == last_t else 7
        last_t, last_d = t, d
    tg["rest_days"] = rest
    tg["b2b"] = (rest == 0).astype(np.int64)

    # ---- starting-lineup ids -------------------------------------------------------------------
    lineup_key = {}
    for i in range(n):
        if pg["started"][i]:
            lineup_key.setdefault(int(pg_gt[i]), []).append(int(pg["player_id"][i]))
    lineup_ids: Dict[tuple, int] = {}
    lineup_names: Dict[int, str] = {}
    tg_lineup = np.zeros(m, dtype=np.int64)
    for r in range(m):
        ids = tuple(sorted(lineup_key.get(r, [])))
        if ids not in lineup_ids:
            lineup_ids[ids] = len(lineup_ids) + 1
            lineup_names[lineup_ids[ids]] = " / ".join(players[p]["name"].split(" ")[-1] for p in ids) or "(no starters recorded)"
        tg_lineup[r] = lineup_ids[ids]
    tg["lineup_id"] = tg_lineup

    # ---- join context columns onto player rows ---------------------------------------------------
    for c in CTX_COLS:
        pg[c] = tg[c][pg_gt]
    pg["rest_days"] = rest[pg_gt]
    pg["b2b"] = tg["b2b"][pg_gt]
    pg["lineup_id"] = tg_lineup[pg_gt]
    pg["win"] = tg["wins"][pg_gt]
    pg["wins"] = pg["win"].copy()
    pg["gp"] = (pg["min"] > 0).astype(float)
    pg["gs"] = pg["started"].astype(float)
    pg["ot"] = tg["ot"][pg_gt]
    # row-level derived additive columns
    pg["gmsc"] = (pg["pts"] + 0.4 * pg["fgm"] - 0.7 * pg["fga"] - 0.4 * (pg["fta"] - pg["ftm"]) + 0.7 * pg["oreb"] + 0.3 * pg["dreb"]
                  + pg["stl"] + 0.7 * pg["ast"] + 0.7 * pg["blk"] - 0.4 * pg["pf"] - pg["tov"])
    cats = np.stack([pg["pts"], pg["reb"], pg["ast"], pg["stl"], pg["blk"]], axis=1)
    n10 = (cats >= 10).sum(axis=1)
    pg["dd"] = (n10 >= 2).astype(float)
    pg["td"] = (n10 >= 3).astype(float)
    pg["pie_num"] = (pg["pts"] + pg["fgm"] + pg["ftm"] - pg["fga"] - pg["fta"] + pg["dreb"] + 0.5 * pg["oreb"] + pg["ast"] + pg["stl"] + 0.5 * pg["blk"] - pg["pf"] - pg["tov"])
    tg_den = (tg["pts"] + tg["fgm"] + tg["ftm"] - tg["fga"] - tg["fta"] + tg["dreb"] + 0.5 * tg["oreb"] + tg["ast"] + tg["stl"] + 0.5 * tg["blk"] - tg["pf"] - tg["tov"])
    pg["pie_den"] = (tg_den + tg_den[opp_row])[pg_gt]
    tg["gmsc"] = np.bincount(pg_gt, weights=pg["gmsc"], minlength=m)
    for c in ("dd", "td", "pie_num"):
        tg[c] = np.bincount(pg_gt, weights=pg[c], minlength=m)
    tg["pie_den"] = tg_den + tg_den[opp_row]
    tg["fg2m"] = tg["fgm"] - tg["fg3m"]

    ds = Dataset(pg=pg, tg=tg, players=players, teams=teams, coaches=coaches, games=games, seasons=seasons, meta=meta)
    ds.pg_gt = pg_gt
    ds.tg_index = key_to_row
    ds.lineup_ids = lineup_ids
    ds.lineup_names = lineup_names
    return ds


def played_flag(ds: Dataset, player_id: int) -> np.ndarray:
    """Boolean array over team-game rows: did `player_id` play (min > 0) in that team-game?"""
    if player_id in ds.played_cache:
        return ds.played_cache[player_id]
    flag = np.zeros(len(ds.tg["game_id"]), dtype=bool)
    sel = (ds.pg["player_id"] == player_id) & (ds.pg["min"] > 0)
    flag[ds.pg_gt[sel]] = True
    ds.played_cache[player_id] = flag
    return flag


def roster_of(ds: Dataset, player_id: int, team_games_mask: Optional[np.ndarray] = None) -> int:
    return ds.players[player_id]["team_id"]
