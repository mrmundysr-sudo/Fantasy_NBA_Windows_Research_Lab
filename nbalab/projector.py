"""Persist projections (append-only), recompute on events, and build the fantasy picks views."""
from __future__ import annotations

import json
from datetime import date
from typing import Callable, Dict, List, Optional

import numpy as np

from .features import asof_for_game
from .models import DRAW_STATS, LabModel, fantasy_summary, pack_draws, unpack_draws
from .store import Store, utcnow

CATEGORIES = {
    "overall": "Overall expected fantasy production (mean)",
    "upside": "Upside (90th percentile outcome)",
    "consistency": "Consistency (low relative spread, meaningful volume)",
    "minutes": "Minutes opportunity (projected minutes)",
    "matchup": "Matchup & pace edge (projected points gained)",
    "defense": "Defensive specialists (steals + blocks)",
    "injury_opportunity": "Injury opportunities (minutes gained from teammates out)",
    "value": "Value (needs cost data)",
}


class Projector:
    def __init__(self, store: Store, ds, fb, cache, get_model: Callable[[], LabModel], events=None, clock: Optional[Callable[[], str]] = None):
        self.store, self.ds, self.fb, self.cache = store, ds, fb, cache
        self.get_model = get_model
        self.events = events
        self.clock = clock or utcnow

    # ---------------------------------------------------------------------------------------------
    def project_team(self, game_id: int, team_id: int, as_of: Optional[str] = None, trigger_event_id: Optional[int] = None) -> int:
        model = self.get_model()
        as_of = as_of or self.clock()
        tp = model.predict_team(game_id, team_id, as_of=as_of, n_draws=model.n_draws)
        feats = {f["player_id"]: f for f in self.cache.get(game_id, team_id, as_of)}
        made = utcnow()
        conflicts = {}
        if self.events:
            for c in self.events.conflicts([game_id]):
                conflicts[c["player_id"]] = c
        fresh = self.events.team_input_freshness(game_id, team_id) if self.events else {}
        n = 0
        for j, pid in enumerate(tp.order):
            f, sm = feats[pid], tp.players[pid]
            feat_json = {k: (v if not isinstance(v, (np.floating, np.integer)) else float(v)) for k, v in f.items()}
            src_times = {"as_of": as_of, "status_published_at": f.get("status_published_at"), "status_source": f.get("status_source"),
                         "last_box_date_used": self._last_box_date(pid, f["date_ord"]), "model_trained_through": model.version}
            cur = self.store.execute("INSERT INTO snapshots (game_id, player_id, made_at, as_of, features_json, source_times_json) VALUES (?,?,?,?,?,?)",
                                     (game_id, pid, made, as_of, json.dumps(feat_json, default=float), json.dumps(src_times)))
            explain = model.explain(f, sm)
            if pid in conflicts:
                explain["flags"].append("Conflicting reports: " + "; ".join(f"{r['source']}={r['status']}" for r in conflicts[pid]["reports"]) + ". " + conflicts[pid]["resolution"])
            explain["input_freshness"] = fresh
            self.store.execute(
                "INSERT INTO predictions (game_id, player_id, model_id, model_version, made_at, as_of, trigger_event_id, snapshot_id, p_play, min_mean, min_p10, min_p50, min_p90, stats_json, draws, explain_json, data_label) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (game_id, pid, model.name, model.version, made, as_of, trigger_event_id, cur.lastrowid, sm["p_play"], sm["min_mean"], sm["min_p10"], sm["min_p50"], sm["min_p90"],
                 json.dumps(sm["stats"]), pack_draws(tp.draws[:, j, :]), json.dumps(explain), self.ds.label()))
            n += 1
        return n

    def _last_box_date(self, pid: int, date_ord: int) -> Optional[str]:
        st = self.fb.h.player_state(pid, date_ord)
        return date.fromordinal(st.last_date).isoformat() if st.n_prior else None

    def project_upcoming(self, as_of: Optional[str] = None, days: int = 4) -> dict:
        as_of = as_of or self.clock()
        today = as_of[:10]
        last = date.fromordinal(date.fromisoformat(today).toordinal() + days).isoformat()
        ups = sorted((g["game_date"], gid) for gid, g in self.ds.games.items() if g["status"] == "scheduled" and today <= g["game_date"] <= last)
        n = 0
        for _, gid in ups:
            g = self.ds.games[gid]
            for t in (g["home_team_id"], g["away_team_id"]):
                n += self.project_team(gid, t, as_of)
        return {"games": len(ups), "predictions_written": n, "as_of": as_of}

    def recompute_for_events(self, inserted: List[dict], as_of: str) -> List[dict]:
        done, out = set(), []
        last_eid = {}
        for e in inserted:
            last_eid[(e["game_id"], e["team_id"])] = e["event_id"]
        for (gid, tid), eid in last_eid.items():
            if gid is None or (gid, tid) in done:
                continue
            done.add((gid, tid))
            n = self.project_team(gid, tid, as_of, trigger_event_id=eid)
            out.append({"game_id": gid, "team_id": tid, "predictions_written": n, "trigger_event_id": eid})
        return out

    # ---------------------------------------------------------------------------------------------
    def latest(self, game_ids: Optional[List[int]] = None, model_id: Optional[str] = None) -> List[dict]:
        q = ("SELECT p.* FROM predictions p JOIN (SELECT MAX(pred_id) mid FROM predictions GROUP BY game_id, player_id, model_id) m ON p.pred_id=m.mid")
        conds, params = [], []
        if game_ids:
            conds.append("p.game_id IN (%s)" % ",".join("?" * len(game_ids)))
            params += game_ids
        if model_id:
            conds.append("p.model_id=?")
            params.append(model_id)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        return [dict(r) for r in self.store.query(q, tuple(params))]

    def player_projection(self, pid: int) -> Optional[dict]:
        ups = sorted((g["game_date"], gid) for gid, g in self.ds.games.items() if g["status"] == "scheduled" and pid and
                     self.ds.players[pid]["team_id"] in (g["home_team_id"], g["away_team_id"]))
        for _, gid in ups:
            rows = self.latest([gid])
            for r in rows:
                if r["player_id"] == pid:
                    return r
        return None

    def projections_for_engine(self, rules: Dict[str, float]):
        """Callable used by the engine's projected metrics (next scheduled game per player)."""
        def fn(pids: List[int]) -> Dict[int, dict]:
            out = {}
            for pid in pids:
                r = self.player_projection(int(pid))
                if not r:
                    continue
                dr = unpack_draws(r["draws"])
                fs = fantasy_summary(dr, rules)
                st = json.loads(r["stats_json"])
                out[int(pid)] = {"proj_min": r["min_mean"], "proj_pts": st["pts"]["mean"], "proj_reb": st["reb"]["mean"], "proj_ast": st["ast"]["mean"],
                                 "proj_fpts": fs["mean"], "proj_fpts_p90": fs["p90"]}
            return out
        return fn

    # ---------------------------------------------------------------------------------------------
    def costs(self) -> Dict[int, float]:
        return {int(k): float(v) for k, v in (self.store.get_meta("costs", {}) or {}).items()}

    def import_costs(self, rows: List[dict], source: str) -> dict:
        by_name = {p["name"].lower(): pid for pid, p in self.ds.players.items()}
        costs, bad = {}, []
        for r in rows:
            pid = by_name.get(str(r.get("player", "")).strip().lower())
            try:
                c = float(r["cost"])
            except (KeyError, ValueError, TypeError):
                bad.append(r)
                continue
            if pid is None or c <= 0:
                bad.append(r)
                continue
            costs[str(pid)] = c
        self.store.set_meta("costs", costs)
        self.store.set_meta("costs_source", source)
        return {"imported": len(costs), "rejected": len(bad), "source": source}

    def picks(self, scoring: dict, category: str = "overall", query: str = "", days: int = 1, team_id: Optional[int] = None,
              position: Optional[str] = None, min_p_play: float = 0.0, limit: int = 40, as_of: Optional[str] = None) -> dict:
        if category not in CATEGORIES:
            raise ValueError(f"unknown category '{category}'")
        as_of = as_of or self.clock()
        today = as_of[:10]
        ups = sorted((g["game_date"], gid) for gid, g in self.ds.games.items() if g["status"] == "scheduled" and g["game_date"] >= today)
        if not ups:
            return {"rows": [], "message": "No scheduled games after the lab clock.", "category": category}
        dates = sorted({d for d, _ in ups})[:max(days, 1)]
        gids = [gid for d, gid in ups if d in dates]
        preds = self.latest(gids)
        if not preds:
            return {"rows": [], "message": "No projections stored yet for these games. Run projections first.", "category": category}
        costs = self.costs()
        if category == "value" and not costs:
            return {"rows": [], "category": category, "message": "Value ranking is unavailable: no cost data loaded. Import a CSV of player costs (Picks panel) - none are fabricated."}
        q = query.strip().lower()
        rules = scoring["rules"]
        rows = []
        for r in preds:
            pid = r["player_id"]
            p = self.ds.players[pid]
            g = self.ds.games[r["game_id"]]
            tm = self.ds.teams[p["team_id"]]
            opp_id = g["away_team_id"] if g["home_team_id"] == p["team_id"] else g["home_team_id"]
            opp = self.ds.teams[opp_id]
            if team_id and p["team_id"] != team_id:
                continue
            if position and p["position"] != position:
                continue
            if q and q not in p["name"].lower() and q not in tm["name"].lower() and q not in tm["abbr"].lower() and q not in opp["name"].lower():
                continue
            if r["p_play"] < min_p_play:
                continue
            dr = unpack_draws(r["draws"])
            fs = fantasy_summary(dr, rules)
            st = json.loads(r["stats_json"])
            ex = json.loads(r["explain_json"] or "{}")
            snap = self.store.one("SELECT features_json FROM snapshots WHERE snapshot_id=?", (r["snapshot_id"],))
            feat = json.loads(snap["features_json"]) if snap else {}
            base_min = feat.get("ewm_min", 0.0)
            sc = {"overall": fs["mean"], "upside": fs["p90"], "minutes": r["min_mean"], "defense": st["stl"]["mean"] + st["blk"]["mean"]}
            matchup_gain = fs["mean"] * ((ex.get("matchup_efficiency_multiplier", 1.0) * ex.get("pace_multiplier", 1.0)) - 1.0)
            sc["matchup"] = matchup_gain
            sc["consistency"] = fs["consistency"] if fs["mean"] >= 15 and r["p_play"] >= 0.8 else -1.0
            opp_gain = ex.get("min_if_play", 0.0) - base_min if feat.get("vacated_min", 0) > 0 and base_min > 0 else -99.0
            sc["injury_opportunity"] = opp_gain * r["p_play"] if opp_gain > -50 else -99.0
            cost = costs.get(pid)
            sc["value"] = (fs["mean"] / cost * 1000.0) if cost else -1.0
            rows.append({
                "player_id": pid, "player": p["name"], "team": tm["abbr"], "team_name": tm["name"], "position": p["position"], "opponent": opp["abbr"],
                "home": g["home_team_id"] == p["team_id"], "game_id": r["game_id"], "game_date": g["game_date"],
                "p_play": round(r["p_play"], 3), "min_mean": round(r["min_mean"], 1), "min_p10": round(r["min_p10"], 1), "min_p90": round(r["min_p90"], 1),
                "fantasy": {k: round(v, 2) for k, v in fs.items()},
                "stats": {k: round(st[k]["mean"], 2) for k in ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m")},
                "dd_prob": round(st["dd_prob"], 3), "score": round(float(sc[category]), 3), "cost": cost,
                "status": ex.get("flags") and feat.get("status"), "status_detail": feat.get("status_detail"),
                "drivers": ex.get("minutes_drivers", []), "flags": ex.get("flags", []), "input_freshness": ex.get("input_freshness"),
                "history_games": ex.get("history_games"), "model_version": r["model_version"], "as_of": r["as_of"], "made_at": r["made_at"],
                "trigger_event_id": r["trigger_event_id"], "data_label": r["data_label"], "pred_id": r["pred_id"],
                "vacated_min": feat.get("vacated_min", 0.0),
            })
        valid = [x for x in rows if x["score"] > -50 and not (category in ("consistency", "value") and x["score"] < 0)]
        valid.sort(key=lambda x: -x["score"])
        for i, x in enumerate(valid, 1):
            x["rank"] = i
        return {"rows": valid[:limit], "category": category, "category_label": CATEGORIES[category], "scoring": {"id": scoring["id"], "version": scoring["version"], "name": scoring["name"]},
                "game_dates": dates, "n_candidates": len(rows), "as_of": as_of, "data_label": self.ds.label(),
                "disclaimer": "SYNTHETIC DEMONSTRATION - these are not real players or real picks." if self.ds.synthetic else "Model output for private research; not advice."}
