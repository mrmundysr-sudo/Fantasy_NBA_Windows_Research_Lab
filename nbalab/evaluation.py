"""Chronological out-of-sample evaluation, model tournament, promotion rules, and the model registry.

Rules enforced here (PROJECT_VISION.md "Modeling and auditing"):
  * walk-forward only: a model is refit at each fold start using data strictly before that fold;
  * every candidate is compared with simple baselines on the SAME rows;
  * MAE / RMSE / bias plus interval calibration are reported, sliced by role, injury context,
    rest, matchup, team and coach;
  * promotion requires a measured improvement whose paired-bootstrap interval excludes zero and no calibration
    regression; otherwise the challenger is recorded as rejected;
  * the registry is append-only (a SQLite trigger blocks edits); rollback appends a new record.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .dataset import Dataset
from .features import FeatureBuilder, RATE_STATS
from .models import DRAW_STATS, FeatureCache, LabModel, PlayModel, RateModel, _actuals
from .scoring import PRESETS, score
from .store import Store, utcnow

EVAL_RULES = PRESETS["classic_points"]["rules"]
EVAL_STATS = ["min", "pts", "reb", "ast", "stl", "blk", "tov", "fg3m"]


# ----------------------------------------------------------------------------------------------------------
class MeanCandidate:
    """Mean-only candidates (baselines and the shrunk-EWMA role model). They cannot be the picks champion."""
    distributional = False

    def __init__(self, ds: Dataset, fb: FeatureBuilder, cache: FeatureCache, name: str, minutes_mode: str, rate_mode: str, use_play_model: bool = False):
        self.ds, self.fb, self.cache = ds, fb, cache
        self.name, self.minutes_mode, self.rate_mode, self.use_play = name, minutes_mode, rate_mode, use_play_model
        self.rate = RateModel()
        self.rate._h = fb.h
        self.play = PlayModel()
        self.version = name

    def fit(self, cutoff_ord: int) -> dict:
        self.rate.fit(self.ds, self.fb, cutoff_ord)
        if self.use_play:
            idx = _actuals(self.ds)
            rows, played = [], []
            for gid, g in self.ds.games.items():
                if g["status"] != "final" or date.fromisoformat(g["game_date"]).toordinal() >= cutoff_ord:
                    continue
                for t in (g["home_team_id"], g["away_team_id"]):
                    for r in self.cache.get(gid, t):
                        if r["n_prior"] >= 3:
                            rows.append(r)
                            i = idx.get((gid, r["player_id"]))
                            played.append(i is not None and self.ds.pg["min"][i] > 0)
            self.play.fit(rows, np.array(played))
        self.version = f"{self.name}@{date.fromordinal(cutoff_ord - 1).isoformat()}"
        return {}

    def _minutes(self, f: dict) -> float:
        if f["n_prior"] == 0:
            return 10.0
        if self.minutes_mode == "season":
            return f["mean_all"]
        if self.minutes_mode == "last5":
            return f["mean5"]
        return f["ewm_min"] * (f["restr_ratio"] if f["restr_flag"] else 1.0)

    def _rates(self, f: dict) -> Dict[str, float]:
        st = self.fb.h.player_state(f["player_id"], f["date_ord"])
        ix = {s: i for i, s in enumerate(RATE_STATS)}
        pos = self.ds.players[f["player_id"]]["position"]
        pri = self.rate.prior[pos]
        if self.rate_mode == "shrunk":
            rt = self.rate.rates(f["player_id"], f["date_ord"], f, self.ds, self.fb)
            return rt
        if st.n_prior and self.rate_mode == "cum" and st.cum_min > 0:
            arr, den = st.cum, st.cum_min
        elif st.n_prior and st.dec_min > 0:
            arr, den = st.dec, st.dec_min
        else:
            return self.rate.rates(f["player_id"], f["date_ord"], {"team_pace": 0.0, "opp_pace": 0.0, "opp_def": 0.0, "home": 0}, self.ds, self.fb)
        per = {s: arr[ix[s]] / den for s in RATE_STATS}
        return {"direct": per}

    def predict_team(self, game_id: int, team_id: int, as_of: Optional[str] = None, **_) -> Dict[int, dict]:
        out = {}
        for f in self.cache.get(game_id, team_id, as_of):
            m = self._minutes(f)
            p = self.play.p(f) if self.use_play else (0.0 if f["status"] == "out" else 1.0)
            rt = self._rates(f)
            if "direct" in rt:
                d = rt["direct"]
                pts = 2 * (d["fgm"] - d["fg3m"]) + 3 * d["fg3m"] + d["ftm"]
                st = {"min": m, "pts": pts * m, "reb": (d["oreb"] + d["dreb"]) * m, "ast": d["ast"] * m, "stl": d["stl"] * m, "blk": d["blk"] * m,
                      "tov": d["tov"] * m, "fg3m": d["fg3m"] * m}
            else:
                ms = self.rate.mean_stats(rt, m)
                st = {k: ms[k] for k in EVAL_STATS}
            row = {"p_play": p, "min_if_play": m}
            for k in EVAL_STATS:
                row[k] = p * st[k]
            row["fpts"] = p * float(score({k: np.array([v]) for k, v in {**st, "fgm": 0, "fga": 0, "ftm": 0, "fta": 0, "oreb": 0, "dreb": 0, "pf": 0}.items()}, EVAL_RULES)[0])
            out[f["player_id"]] = row
        return out


class LabCandidate(LabModel):
    distributional = True

    def predict_team_eval(self, game_id: int, team_id: int, as_of: Optional[str] = None, n_draws: int = 100) -> Dict[int, dict]:
        tp = self.predict_team(game_id, team_id, as_of, n_draws=n_draws)
        out = {}
        for j, pid in enumerate(tp.order):
            dr = tp.draws[:, j, :]
            st = {s: dr[:, i] for i, s in enumerate(DRAW_STATS)}
            f = score(st, EVAL_RULES)
            sm = tp.players[pid]
            row = {"p_play": sm["p_play"], "min_if_play": sm["min_if_play"], "fpts": float(f.mean()),
                   "fpts_q": np.percentile(f, [10, 25, 50, 75, 90]).tolist(),
                   "min_q": np.percentile(st["min"], [10, 25, 50, 75, 90]).tolist()}
            for k in EVAL_STATS:
                row[k] = float(st[k].mean())
            out[pid] = row
        return out

    def predict_team_mean(self, game_id, team_id, as_of=None, **kw):
        return self.predict_team_eval(game_id, team_id, as_of, kw.get("n_draws", 100))


# ----------------------------------------------------------------------------------------------------------
class ModelRegistry:
    """Append-only log of model lifecycle events with a derived champion/rollback state."""

    def __init__(self, store: Store, default_champion: str = "lab_v1"):
        self.store = store
        self.default = default_champion
        if not self.store.one("SELECT 1 FROM model_registry"):
            self.record(default_champion, "initial", "initial_default", {"validated": False}, "Default champion before any measured tournament; not yet validated.")

    def record(self, model_id: str, version: str, action: str, metrics: dict, note: str = "") -> None:
        self.store.execute("INSERT INTO model_registry (model_id, version, action, at, metrics_json, note) VALUES (?,?,?,?,?,?)",
                           (model_id, version, action, utcnow(), json.dumps(metrics, default=float), note))

    def history(self, limit: int = 200) -> List[dict]:
        return [{"seq": r["seq"], "model_id": r["model_id"], "version": r["version"], "action": r["action"], "at": r["at"],
                 "metrics": json.loads(r["metrics_json"] or "{}"), "note": r["note"]}
                for r in self.store.query("SELECT * FROM model_registry ORDER BY seq DESC LIMIT ?", (limit,))]

    def champion_stack(self) -> List[str]:
        """Replay the log: promotions push, rollbacks pop."""
        stack: List[str] = []
        for r in self.store.query("SELECT * FROM model_registry ORDER BY seq"):
            if r["action"] == "initial_default":
                stack = [r["model_id"]]
            elif r["action"] == "promote":
                stack.append(r["model_id"])
            elif r["action"] == "rollback" and len(stack) > 1:
                stack.pop()
        return stack or [self.default]

    def champion(self) -> str:
        return self.champion_stack()[-1]

    def rollback(self, note: str = "manual rollback") -> str:
        stack = self.champion_stack()
        if len(stack) < 2:
            raise ValueError("nothing to roll back to")
        self.record(stack[-1], "n/a", "rollback", {"to": stack[-2]}, note)
        return stack[-2]


# ----------------------------------------------------------------------------------------------------------
def _fold_bounds(ds: Dataset, n_folds: int = 3, start_frac: float = 0.5) -> List[Tuple[int, int]]:
    dates = sorted({date.fromisoformat(g["game_date"]).toordinal() for g in ds.games.values() if g["status"] == "final"})
    k0 = int(len(dates) * start_frac)
    cuts = [dates[k0 + int((len(dates) - k0) * i / n_folds)] for i in range(n_folds)] + [dates[-1] + 1]
    return [(cuts[i], cuts[i + 1]) for i in range(n_folds)]


def _role(ewm: float) -> str:
    return "starter(28+)" if ewm >= 28 else ("rotation(15-28)" if ewm >= 15 else "bench(<15)")


def run_tournament(ds: Dataset, fb: FeatureBuilder, cache: FeatureCache, candidates: Sequence, champion_name: str = "lab_v1",
                   n_folds: int = 3, n_draws: int = 100, progress: Optional[Callable[[str], None]] = None, max_games_per_fold: Optional[int] = None) -> dict:
    log = progress or (lambda s: None)
    idx = _actuals(ds)
    folds = _fold_bounds(ds, n_folds)
    rows: Dict[str, List[dict]] = {c.name: [] for c in candidates}
    meta_rows: List[dict] = []
    fit_info = {}
    for fi, (a, b) in enumerate(folds):
        log(f"fold {fi + 1}/{len(folds)}: fit on data before {date.fromordinal(a).isoformat()}")
        games = sorted(((g["game_date"], gid) for gid, g in ds.games.items() if g["status"] == "final" and a <= date.fromisoformat(g["game_date"]).toordinal() < b))
        if max_games_per_fold:
            games = games[:max_games_per_fold]
        for c in candidates:
            fit_info[c.name] = c.fit(a)
        first = True
        for _, gid in games:
            g = ds.games[gid]
            for t in (g["home_team_id"], g["away_team_id"]):
                preds = {}
                for c in candidates:
                    preds[c.name] = c.predict_team_eval(gid, t, n_draws=n_draws) if getattr(c, "distributional", False) else c.predict_team(gid, t)
                feats = {f["player_id"]: f for f in cache.get(gid, t)}
                for pid, f in feats.items():
                    if f["n_prior"] < 3 or f["status"] == "out":
                        continue
                    i = idx.get((gid, pid))
                    act = {k: (float(ds.pg[k][i]) if i is not None else 0.0) for k in EVAL_STATS}
                    act["fpts"] = float(score({k: np.array([float(ds.pg[k][i]) if i is not None else 0.0]) for k in ("pts", "fgm", "fga", "fg3m", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf")}, EVAL_RULES)[0]) if i is not None else 0.0
                    act["played"] = float(i is not None and ds.pg["min"][i] > 0)
                    ctx = {"gid": gid, "pid": pid, "team": t, "coach": f["coach_id"], "fold": fi, "role": _role(f["ewm_min"]),
                           "inj": "returning" if f["returning"] else ("restricted" if f["restr_flag"] else ("starter_out" if f["vacated_starters"] > 0 else "normal")),
                           "b2b": "b2b" if f["b2b"] else "rested", "opp_def": f["opp_def"], "act": act}
                    meta_rows.append(ctx)
                    for c in candidates:
                        p = preds[c.name].get(pid)
                        rows[c.name].append(p)
    log("computing metrics")
    n = len(meta_rows)
    # opponent defence terciles
    od = np.array([m["opp_def"] for m in meta_rows])
    t1, t2 = np.percentile(od[od > 0], [33, 67]) if (od > 0).any() else (0, 0)
    for m in meta_rows:
        m["matchup"] = "unknown" if m["opp_def"] <= 0 else ("tough D" if m["opp_def"] < t1 else ("average D" if m["opp_def"] < t2 else "soft D"))
    A = {k: np.array([m["act"][k] for m in meta_rows]) for k in EVAL_STATS + ["fpts", "played"]}
    result = {"n_rows": n, "folds": [{"start": date.fromordinal(a).isoformat(), "end": date.fromordinal(b - 1).isoformat()} for a, b in folds],
              "eval_scoring": "classic_points v1 (linear, for model comparison)", "models": {}, "fit_info": fit_info}
    abs_err: Dict[str, Dict[str, np.ndarray]] = {}
    for c in candidates:
        P = rows[c.name]
        ok = np.array([p is not None for p in P])
        pred = {k: np.array([p[k] if p else np.nan for p in P]) for k in EVAL_STATS + ["fpts", "p_play"]}
        metrics = {}
        abs_err[c.name] = {}
        for k in EVAL_STATS + ["fpts"]:
            e = pred[k] - A[k]
            metrics[k] = {"mae": float(np.nanmean(np.abs(e))), "rmse": float(np.sqrt(np.nanmean(e ** 2))), "bias": float(np.nanmean(e))}
            abs_err[c.name][k] = np.abs(e)
        metrics["p_play_brier"] = float(np.nanmean((pred["p_play"] - A["played"]) ** 2))
        if getattr(c, "distributional", False):
            fq = np.array([p["fpts_q"] if p else [np.nan] * 5 for p in P])
            mq = np.array([p["min_q"] if p else [np.nan] * 5 for p in P])
            levels = [0.1, 0.25, 0.5, 0.75, 0.9]
            metrics["calibration"] = {
                "fpts_cover_80": float(np.nanmean((A["fpts"] >= fq[:, 0]) & (A["fpts"] <= fq[:, 4]))),
                "min_cover_80": float(np.nanmean((A["min"] >= mq[:, 0]) & (A["min"] <= mq[:, 4]))),
                "fpts_reliability": [{"nominal": lv, "observed": float(np.nanmean(A["fpts"] <= fq[:, i]))} for i, lv in enumerate(levels)],
                "min_reliability": [{"nominal": lv, "observed": float(np.nanmean(A["min"] <= mq[:, i]))} for i, lv in enumerate(levels)],
            }
        sl: Dict[str, Dict[str, dict]] = {}
        for sname in ("role", "inj", "b2b", "matchup"):
            groups = defaultdict(list)
            for i, m in enumerate(meta_rows):
                groups[m[sname]].append(i)
            sl[sname] = {g: {"n": len(ix_), "min_mae": float(np.nanmean(abs_err[c.name]["min"][ix_])), "fpts_mae": float(np.nanmean(abs_err[c.name]["fpts"][ix_]))}
                         for g, ix_ in sorted(groups.items())}
        for sname in ("team", "coach"):
            groups = defaultdict(list)
            for i, m in enumerate(meta_rows):
                groups[m[sname]].append(i)
            sl[sname] = {str(g): {"n": len(ix_), "min_mae": float(np.nanmean(abs_err[c.name]["min"][ix_])), "fpts_mae": float(np.nanmean(abs_err[c.name]["fpts"][ix_]))}
                         for g, ix_ in sorted(groups.items()) if len(ix_) >= 30}
        result["models"][c.name] = {"distributional": bool(getattr(c, "distributional", False)), "version": c.version, "metrics": metrics, "slices": sl}
    # paired comparison vs champion and promotion verdicts
    champ = champion_name if champion_name in abs_err else candidates[0].name
    rng = np.random.default_rng(0)
    result["champion"] = champ
    result["comparisons"] = {}
    for c in candidates:
        if c.name == champ:
            continue
        comp = {}
        for k in ("min", "fpts"):
            d = abs_err[champ][k] - abs_err[c.name][k]  # positive = challenger better
            d = d[~np.isnan(d)]
            boots = [float(np.mean(d[rng.integers(0, len(d), len(d))])) for _ in range(400)]
            comp[k] = {"mean_improvement": float(d.mean()), "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
                       "relative_pct": float(100 * d.mean() / max(np.mean(abs_err[champ][k][~np.isnan(abs_err[champ][k])]), 1e-9))}
        cal = result["models"][c.name]["metrics"].get("calibration")
        cal_ok = bool(cal) and abs(cal["fpts_cover_80"] - 0.8) <= 0.07 and abs(cal["min_cover_80"] - 0.8) <= 0.07
        promote = bool(getattr(c, "distributional", False)) and comp["fpts"]["ci95"][0] > 0 and comp["min"]["ci95"][0] > 0 and cal_ok
        reason = []
        if not getattr(c, "distributional", False):
            reason.append("mean-only model cannot drive picks (no distribution)")
        if not comp["fpts"]["ci95"][0] > 0:
            reason.append("fantasy MAE improvement not distinguishable from zero")
        if not comp["min"]["ci95"][0] > 0:
            reason.append("minutes MAE improvement not distinguishable from zero")
        if cal is None or not cal_ok:
            reason.append("80% interval coverage outside 73%-87%")
        comp["promote"] = promote
        comp["reasons"] = reason if not promote else ["measured improvement with no calibration regression"]
        result["comparisons"][c.name] = comp
    return result
