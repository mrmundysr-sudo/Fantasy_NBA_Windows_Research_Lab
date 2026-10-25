"""Player and team profile builders (all numbers come from the calculation engine)."""
from __future__ import annotations

import json
from datetime import date
from typing import List, Optional

import numpy as np

from .models import fantasy_summary, unpack_draws

PLAYER_BASE = [("gp", "raw"), ("gs", "raw"), ("min", "per_game"), ("pts", "per_game"), ("reb", "per_game"), ("ast", "per_game"), ("stl", "per_game"),
               ("blk", "per_game"), ("tov", "per_game"), ("fg_pct", "raw"), ("fg3_pct", "raw"), ("ft_pct", "raw"), ("ts_pct", "raw"), ("efg_pct", "raw"),
               ("usg_pct", "raw"), ("ast_pct", "raw"), ("reb_pct", "raw"), ("tov_pct", "raw"), ("stl_pct", "raw"), ("blk_pct", "raw"), ("pie", "raw"),
               ("pts", "per36"), ("reb", "per36"), ("ast", "per36"), ("gmsc", "per_game"), ("touches", "per_game"), ("drives", "per_game"), ("pot_ast", "per_game")]
TEAM_BASE = [("gp", "raw"), ("win_pct", "raw"), ("ortg", "raw"), ("drtg", "raw"), ("nrtg", "raw"), ("pace", "raw"), ("efg_pct", "raw"), ("ts_pct", "raw"),
             ("tov_pct", "raw"), ("three_rate", "raw"), ("fta_rate", "raw"), ("pts", "per_game"), ("opp_pts_pg", "raw"), ("ast", "per_game"), ("reb", "per_game")]


def _mq(items):
    return [{"id": a, "transform": b} for a, b in items]


def _vals(res):
    if not res["rows"]:
        return {}
    r = res["rows"][0]
    return {"values": r["values"], "ci90": r.get("ci90", {}), "games": r["games"], "minutes": r["minutes"]}


def _cols(res):
    return [{"label": c["label"], "kind": c["kind"], "unit": c["unit"], "id": c["id"], "transform": c["transform"]} for c in res["columns"]]


def search(lab, q: str, limit: int = 12) -> dict:
    ql = q.strip().lower()
    ds = lab.ds
    out = {"players": [], "teams": [], "coaches": [], "metrics": []}
    if not ql:
        return out
    for pid, p in ds.players.items():
        if ql in p["name"].lower():
            out["players"].append({"id": pid, "name": p["name"], "team": ds.teams[p["team_id"]]["abbr"], "position": p["position"]})
    for tid, t in ds.teams.items():
        if ql in t["name"].lower() or ql == t["abbr"].lower():
            out["teams"].append({"id": tid, "name": t["name"], "abbr": t["abbr"]})
    for cid, c in ds.coaches.items():
        if ql in c["name"].lower():
            out["coaches"].append({"id": cid, "name": c["name"]})
    from . import registry
    out["metrics"] = [{"id": m.id, "name": m.name, "kind": m.kind, "category": m.category, "status": m.status} for m in registry.search(q, 10)]
    for k in ("players", "teams", "coaches"):
        out[k] = out[k][:limit]
    return out


def player_profile(lab, pid: int) -> dict:
    ds, eng = lab.ds, lab.engine
    if pid not in ds.players:
        raise KeyError(pid)
    p = ds.players[pid]
    team = ds.teams[p["team_id"]]
    seasons = {}
    for s in ds.seasons:
        res = eng.run({"entity": "player", "ids": [pid], "metrics": _mq(PLAYER_BASE), "window": {"seasons": [s]}, "min_games": 1, "limit": 1})
        if res["rows"]:
            seasons[s] = {**_vals(res), "columns": _cols(res)}
    career = eng.run({"entity": "player", "ids": [pid], "metrics": _mq(PLAYER_BASE), "min_games": 1, "limit": 1})
    # last 30 game minutes + game log
    pg = ds.pg
    m = (pg["player_id"] == pid) & (pg["min"] > 0)
    idx = np.nonzero(m)[0]
    idx = idx[np.lexsort((pg["game_id"][idx], pg["date"][idx]))]
    log = []
    for i in idx[-25:][::-1]:
        g = ds.games[int(pg["game_id"][i])]
        opp = ds.teams[int(pg["opp_team_id"][i])]["abbr"]
        log.append({"game_id": int(pg["game_id"][i]), "date": g["game_date"], "opp": opp, "home": bool(pg["home"][i]), "result": "W" if pg["win"][i] > 0 else "L",
                    "min": round(float(pg["min"][i]), 1), "pts": int(pg["pts"][i]), "reb": int(pg["reb"][i]), "ast": int(pg["ast"][i]), "stl": int(pg["stl"][i]),
                    "blk": int(pg["blk"][i]), "tov": int(pg["tov"][i]), "started": bool(pg["started"][i]), "fg": f"{int(pg['fgm'][i])}/{int(pg['fga'][i])}"})
    minutes_series = [{"date": ds.games[int(pg["game_id"][i])]["game_date"], "min": round(float(pg["min"][i]), 1)} for i in idx[-40:]]
    # games missed (injury / rest / DNP) from availability
    miss = [dict(r) for r in lab.store.query("SELECT a.game_id, g.game_date, a.status, a.reason FROM availability a JOIN games g ON g.game_id=a.game_id WHERE a.player_id=? ORDER BY g.game_date DESC LIMIT 15", (pid,))]
    # next-game projection
    proj = None
    r = lab.projector.player_projection(pid)
    if r:
        dr = unpack_draws(r["draws"])
        sc = lab.scoring.get("classic_points")
        st = json.loads(r["stats_json"])
        g = ds.games[r["game_id"]]
        opp_id = g["away_team_id"] if g["home_team_id"] == p["team_id"] else g["home_team_id"]
        proj = {"game_id": r["game_id"], "date": g["game_date"], "opponent": ds.teams[opp_id]["abbr"], "home": g["home_team_id"] == p["team_id"],
                "p_play": r["p_play"], "min": {"mean": r["min_mean"], "p10": r["min_p10"], "p50": r["min_p50"], "p90": r["min_p90"]},
                "stats": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in st.items() if isinstance(v, dict) and k in ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m")},
                "fantasy_classic": {k: round(v, 2) for k, v in fantasy_summary(dr, sc["rules"]).items()}, "model_version": r["model_version"], "as_of": r["as_of"],
                "explain": json.loads(r["explain_json"] or "{}"), "synthetic": ds.synthetic}
    # inferred style profile (z-scores vs players with >=300 minutes) - labelled inferred, not observed
    pop = eng.run({"entity": "player", "metrics": [{"id": "three_rate"}, {"id": "fta_rate"}, {"id": "ast_pct"}, {"id": "reb_pct"}, {"id": "blk_pct"}, {"id": "stl_pct"}, {"id": "usg_pct"}],
                   "min_minutes": 300, "limit": 5000, "uncertainty": False})
    style = []
    if pop["rows"]:
        labs = [c["label"] for c in pop["columns"]]
        mat = np.array([[r["values"][l] if r["values"][l] is not None else np.nan for l in labs] for r in pop["rows"]])
        mu, sd = np.nanmean(mat, axis=0), np.nanstd(mat, axis=0)
        mine = next((r for r in pop["rows"] if r["id"] == pid), None)
        names = {"three_rate": "perimeter / three-point shooting tendency", "fta_rate": "foul-drawing / rim pressure tendency", "ast_pct": "playmaking load",
                 "reb_pct": "rebounding", "blk_pct": "rim protection", "stl_pct": "ball pressure / steals", "usg_pct": "offensive usage"}
        if mine:
            for c, mu_, sd_ in zip(pop["columns"], mu, sd):
                v = mine["values"][c["label"]]
                if v is not None and sd_ > 0:
                    style.append({"trait": names.get(c["id"], c["label"]), "z": round(float((v - mu_) / sd_), 2), "value": v, "kind": "inferred"})
    # injury events + status
    events = [dict(r) for r in lab.store.query("SELECT event_id, source, status, detail, published_at, game_id, restriction_min, data_label FROM events WHERE player_id=? ORDER BY published_at DESC LIMIT 10", (pid,))]
    return {"id": pid, "name": p["name"], "team": {"id": team["team_id"], "abbr": team["abbr"], "name": team["name"]}, "position": p["position"], "age": p["age"],
            "height_in": p["height_in"], "dataset_label": ds.label(), "synthetic": ds.synthetic, "seasons": seasons, "career": {**_vals(career), "columns": _cols(career)},
            "game_log": log, "minutes_series": minutes_series, "missed": miss, "projection": proj, "style_inferred": style, "events": events,
            "kinds_note": "Values are observed or derived from box scores; style traits are INFERRED z-scores, not tracked play types."}


def team_profile(lab, tid: int) -> dict:
    ds, eng = lab.ds, lab.engine
    t = ds.teams[tid]
    res = eng.run({"entity": "team", "ids": [tid], "metrics": _mq(TEAM_BASE), "min_games": 1, "limit": 1})
    seasons = {}
    for s in ds.seasons:
        r = eng.run({"entity": "team", "ids": [tid], "metrics": _mq(TEAM_BASE), "window": {"seasons": [s]}, "min_games": 1, "limit": 1})
        if r["rows"]:
            seasons[s] = _vals(r)
    roster = eng.run({"entity": "player", "team_ids": [tid], "metrics": _mq([("gp", "raw"), ("min", "per_game"), ("pts", "per_game"), ("reb", "per_game"), ("ast", "per_game"),
                                                                           ("usg_pct", "raw"), ("ts_pct", "raw"), ("min_share", "raw")]), "sort": {"by": "min_per_game"}, "limit": 20, "uncertainty": False})
    coaches = []
    tg = ds.tg
    mask = tg["team_id"] == tid
    for cid in sorted({int(c) for c in tg["coach_id"][mask]}):
        r = eng.run({"entity": "team", "ids": [tid], "filters": {"coach_ids": [cid]}, "metrics": _mq([("gp", "raw"), ("win_pct", "raw"), ("nrtg", "raw"), ("pace", "raw")]), "min_games": 1, "limit": 1})
        dts = tg["date"][mask & (tg["coach_id"] == cid)]
        if r["rows"]:
            coaches.append({"coach_id": cid, "name": ds.coaches[cid]["name"], "first": date.fromordinal(int(dts.min())).isoformat(), "last": date.fromordinal(int(dts.max())).isoformat(), **_vals(r)})
    lineups = []
    lt = eng.run({"entity": "lineup", "team_ids": [tid], "metrics": _mq([("gp", "raw"), ("nrtg", "raw"), ("ortg", "raw"), ("drtg", "raw")]), "sort": {"by": "gp"}, "limit": 8, "min_games": 3, "uncertainty": False})
    for r in lt["rows"]:
        lineups.append({"lineup": r["name"], "games": r["games"], "values": r["values"]})
    ups = []
    for gid, g in sorted(ds.games.items(), key=lambda kv: kv[1]["game_date"]):
        if g["status"] == "scheduled" and tid in (g["home_team_id"], g["away_team_id"]):
            opp = g["away_team_id"] if g["home_team_id"] == tid else g["home_team_id"]
            ups.append({"game_id": gid, "date": g["game_date"], "opp": ds.teams[opp]["abbr"], "home": g["home_team_id"] == tid, "freshness": lab.events.team_input_freshness(gid, tid)})
    return {"id": tid, "name": t["name"], "abbr": t["abbr"], "conference": t["conference"], "dataset_label": ds.label(), "synthetic": ds.synthetic, "career": _vals(res),
            "columns": _cols(res), "seasons": seasons, "roster": roster["rows"], "roster_columns": _cols(roster), "coaching_eras": coaches,
            "starting_lineups": lineups, "lineup_note": "Lineups are STARTING lineups; on-court lineup data (play-by-play) is not in this dataset.", "upcoming": ups[:6]}
