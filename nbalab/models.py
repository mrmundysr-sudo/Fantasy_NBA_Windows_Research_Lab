"""Projection models: minutes first, then conditional box-score distributions.

Pipeline for one team-game:
  1. P(play): from published status (out / questionable / probable / none) and role, learned from history.
  2. Minutes if playing: ridge regression on pregame features + empirical residual pools (heteroscedastic by role).
  3. Joint simulation: draw availability and minutes for the whole roster, rescale so the team's minutes equal
     240 (+25 per overtime period drawn from history), so a teammate being out redistributes minutes.
  4. Conditional stats: per-minute rates (decayed, shrunk toward position priors) adjusted for pace and opponent,
     negative-binomial volume, binomial shooting, so points = 2*FG2M + 3*FG3M + FTM holds exactly in every draw.
Fantasy scoring is applied to the draws, so any scoring system gets a proper distribution.
"""
from __future__ import annotations

import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .dataset import Dataset
from .features import FeatureBuilder, MIN_FEATURES, RATE_STATS, asof_for_game
from .scoring import score

DRAW_STATS = ["min", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf"]
VOLUME = ["fga", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf"]
MIN_SCALE = 5  # minutes stored in draws at 0.2 min resolution (uint8)
BUCKETS = [0, 10, 18, 26, 32, 60]
FEATURE_LABELS = {
    "ewm_min": "recent minutes trend", "mean5": "last-5-game minutes", "mean_all": "season-long minutes", "start_ewm": "starter role",
    "log_n_prior": "games of history", "g_since": "games missed recently", "returning": "returning from absence", "rest_days": "rest days",
    "b2b": "back-to-back", "home": "home court", "age": "age", "age_b2b": "age-30+ on back-to-back", "abs_spread": "expected blowout risk",
    "vacated_min": "minutes vacated by teammates who are out", "vacated_starters": "starters out", "restr_ratio": "published minutes restriction",
    "restr_flag": "restriction flag", "coach_starter_min": "coach's starter workload", "depth_rank": "rotation depth rank", "top5": "top-5 rotation player",
    "ewm_x_restr": "restriction-adjusted minutes",
}


def _bucket(m: float) -> int:
    for i in range(len(BUCKETS) - 1):
        if BUCKETS[i] <= m < BUCKETS[i + 1]:
            return i
    return len(BUCKETS) - 2


class FeatureCache:
    """Caches per-(game, team) pregame feature rows (each computed leakage-safely and independently)."""

    def __init__(self, fb: FeatureBuilder):
        self.fb = fb
        self.cache: Dict[Tuple[int, int, str, int], List[dict]] = {}

    def get(self, game_id: int, team_id: int, as_of: Optional[str] = None) -> List[dict]:
        g = self.fb.ds.games[game_id]
        as_of = as_of or asof_for_game(g["game_date"])
        key = (game_id, team_id, as_of, self.fb.event_rev)
        if key not in self.cache:
            self.cache[key] = self.fb.team_features(game_id, team_id, as_of)
        return self.cache[key]

    def clear(self):
        self.cache.clear()


def _actuals(ds: Dataset) -> Dict[Tuple[int, int], int]:
    return {(int(g), int(p)): i for i, (g, p) in enumerate(zip(ds.pg["game_id"], ds.pg["player_id"]))}


# ----------------------------------------------------------------------------------------------------------
class MinutesModel:
    def __init__(self):
        self.coef = None
        self.mu = None
        self.sd = None
        self.intercept = 0.0
        self.lam = 10.0
        self.pools: Dict[int, np.ndarray] = {}
        self.cols = MIN_FEATURES

    @staticmethod
    def _X(rows: Sequence[dict], cols) -> np.ndarray:
        return np.array([[r[c] for c in cols] for r in rows], dtype=float)

    @staticmethod
    def _ridge(X, y, lam):
        mu, sd = X.mean(axis=0), X.std(axis=0)
        sd[sd == 0] = 1.0
        Z = (X - mu) / sd
        yb = y.mean()
        A = Z.T @ Z + lam * np.eye(Z.shape[1])
        coef = np.linalg.solve(A, Z.T @ (y - yb))
        return coef, yb, mu, sd

    def fit(self, rows: List[dict], y: np.ndarray, dates: np.ndarray) -> dict:
        X = self._X(rows, self.cols)
        order = np.argsort(dates, kind="stable")
        X, y, dates = X[order], y[order], dates[order]
        cut = int(len(y) * 0.8)
        best, best_lam = None, 10.0
        for lam in (1.0, 10.0, 50.0, 200.0, 1000.0):
            c, b, mu, sd = self._ridge(X[:cut], y[:cut], lam)
            pred = ((X[cut:] - mu) / sd) @ c + b
            mae = float(np.mean(np.abs(pred - y[cut:])))
            if best is None or mae < best:
                best, best_lam = mae, lam
        self.lam = best_lam
        self.coef, self.intercept, self.mu, self.sd = self._ridge(X, y, best_lam)
        resid = y - (((X - self.mu) / self.sd) @ self.coef + self.intercept)
        pred = y - resid
        self.pools = {}
        for b in range(len(BUCKETS) - 1):
            m = np.array([_bucket(p) == b for p in pred])
            r = resid[m]
            if len(r) < 30:
                r = resid
            self.pools[b] = np.sort(r)[:5000] if len(r) <= 5000 else np.sort(np.random.default_rng(0).choice(r, 5000, replace=False))
        return {"lambda": best_lam, "validation_mae_minutes": round(best, 3), "n_train": int(len(y))}

    def predict(self, feat: dict) -> float:
        x = np.array([feat[c] for c in self.cols], dtype=float)
        return float(((x - self.mu) / self.sd) @ self.coef + self.intercept)

    def contributions(self, feat: dict, top: int = 4) -> List[dict]:
        x = np.array([feat[c] for c in self.cols], dtype=float)
        z = (x - self.mu) / self.sd
        contrib = z * self.coef
        order = np.argsort(-np.abs(contrib))[:top]
        return [{"feature": self.cols[i], "label": FEATURE_LABELS.get(self.cols[i], self.cols[i]), "value": round(float(x[i]), 3),
                 "minutes_effect": round(float(contrib[i]), 2)} for i in order if abs(contrib[i]) >= 0.05]


class PlayModel:
    """P(player plays | published status, role). Empirical, with a weak prior."""

    def __init__(self):
        self.table: Dict[Tuple[str, str], Tuple[int, int]] = defaultdict(lambda: (0, 0))

    @staticmethod
    def key(feat: dict) -> Tuple[str, str]:
        st = feat.get("status") or "none"
        if st == "none":
            return ("none", "deep" if feat["depth_rank"] >= 10 else ("rot" if feat["ewm_min"] >= 14 else "bench"))
        return (st, "rot" if feat["ewm_min"] >= 18 else "bench")

    def fit(self, rows: List[dict], played: np.ndarray) -> None:
        tab: Dict[Tuple[str, str], List[int]] = defaultdict(lambda: [0, 0])
        for r, p in zip(rows, played):
            k = self.key(r)
            tab[k][0] += int(p)
            tab[k][1] += 1
        self.table = {k: (v[0], v[1]) for k, v in tab.items()}

    def p(self, feat: dict) -> float:
        k = self.key(feat)
        a, n = self.table.get(k, (0, 0))
        prior = {"out": 0.03, "questionable": 0.5, "probable": 0.9, "none": 0.93}.get(k[0], 0.9)
        strength = 4.0
        return float(np.clip((a + prior * strength) / (n + strength), 0.0, 1.0))


class RateModel:
    """Per-minute rates shrunk toward position priors, with pace and opponent adjustments."""

    def __init__(self, k_rate: float = 120.0):
        self.k_rate = k_rate
        self.prior: Dict[str, dict] = {}
        self.phi: Dict[str, float] = {}
        self.league_pace = 99.5
        self.league_ppp = 110.0
        self.ots = np.array([0.93, 0.065, 0.005])

    def fit(self, ds: Dataset, fb: FeatureBuilder, cutoff_ord: int) -> None:
        pg = ds.pg
        m = (pg["date"] < cutoff_ord) & (pg["min"] > 0)
        pos_of = np.array([ds.players[int(p)]["position"] for p in pg["player_id"]])
        self.prior = {}
        for pos in ("PG", "SG", "SF", "PF", "C"):
            mm = m & (pos_of == pos)
            tot_min = max(pg["min"][mm].sum(), 1.0)
            pr = {s: float(pg[s][mm].sum() / tot_min) for s in RATE_STATS}
            fga, fg3a = pg["fga"][mm].sum(), pg["fg3a"][mm].sum()
            pr["p3"] = float(pg["fg3m"][mm].sum() / max(fg3a, 1))
            pr["p2"] = float((pg["fgm"][mm].sum() - pg["fg3m"][mm].sum()) / max(fga - fg3a, 1))
            pr["pft"] = float(pg["ftm"][mm].sum() / max(pg["fta"][mm].sum(), 1))
            pr["share"] = float(fg3a / max(fga, 1))
            self.prior[pos] = pr
        tg = ds.tg
        mt = tg["date"] < cutoff_ord
        if mt.any():
            self.league_ppp = float(100 * tg["pts"][mt].sum() / tg["tm_poss"][mt].sum())
            self.league_pace = float(np.mean(48 * tg["tm_poss"][mt] / (tg["min"][mt] / 5)))
            ot = tg["ot"][mt]
            c = np.array([(ot == 0).sum(), (ot == 1).sum(), (ot >= 2).sum()], dtype=float)
            self.ots = c / c.sum()
        # dispersion per volume stat, conditional on actual minutes
        idx = np.nonzero(m & (pg["min"] >= 8))[0]
        rng = np.random.default_rng(1)
        if len(idx) > 6000:
            idx = rng.choice(idx, 6000, replace=False)
        h = fb.h
        num = defaultdict(float)
        den = defaultdict(float)
        for i in idx:
            pid, d = int(pg["player_id"][i]), int(pg["date"][i])
            st = h.player_state(pid, d)
            if st.n_prior < 5:
                continue
            feat = {"team_pace": 0.0, "opp_pace": 0.0, "opp_def": 0.0, "home": 0}
            rates = self.rates(pid, d, feat, ds)
            for s in VOLUME:
                mu = rates["vol"][s] * float(pg["min"][i])
                y = float(pg[s][i])
                num[s] += (y - mu) ** 2 - mu
                den[s] += mu * mu
        self.phi = {s: float(max(num[s] / den[s], 0.0)) if den[s] > 0 else 0.0 for s in VOLUME}

    def rates(self, pid: int, date_ord: int, feat: dict, ds: Dataset, fb: Optional[FeatureBuilder] = None, mode: str = "lab") -> dict:
        h = (fb.h if fb else self._h)
        st = h.player_state(pid, date_ord)
        pos = ds.players[pid]["position"]
        pri = self.prior[pos]
        k = self.k_rate
        n = st.n_prior > 0
        dec = st.dec if n else np.zeros(len(RATE_STATS))
        den = st.dec_min if n else 0.0
        ix = {s: i for i, s in enumerate(RATE_STATS)}
        tp, op = feat.get("team_pace", 0.0), feat.get("opp_pace", 0.0)
        pace = 1.0
        if tp > 0 and op > 0:
            pace = float(np.clip(0.5 * (tp + op) / self.league_pace, 0.93, 1.07))
        eff = 1.0
        if feat.get("opp_def", 0.0) > 0:
            eff = 1.0 + 0.8 * (feat["opp_def"] - self.league_ppp) / 100.0 + 0.8 * 0.012 * (1 if feat.get("home") else -0.0)
        vol = {s: ((dec[ix[s]] + k * pri[s]) / (den + k)) * pace for s in VOLUME}
        kk3, kk2, kkf, kks = 80.0, 120.0, 50.0, 100.0
        p3 = (dec[ix["fg3m"]] + kk3 * pri["p3"]) / (dec[ix["fg3a"]] + kk3)
        p2 = ((dec[ix["fgm"]] - dec[ix["fg3m"]]) + kk2 * pri["p2"]) / ((dec[ix["fga"]] - dec[ix["fg3a"]]) + kk2)
        pft = (dec[ix["ftm"]] + kkf * pri["pft"]) / (dec[ix["fta"]] + kkf)
        share = (dec[ix["fg3a"]] + kks * pri["share"]) / (dec[ix["fga"]] + kks)
        return {"vol": vol, "p3": float(np.clip(p3 * eff, 0.05, 0.7)), "p2": float(np.clip(p2 * eff, 0.2, 0.85)), "pft": float(np.clip(pft, 0.3, 0.97)),
                "share": float(np.clip(share, 0.0, 0.9)), "pace_mult": pace, "eff_mult": eff, "n_prior": st.n_prior}

    def mean_stats(self, rate: dict, minutes: float) -> dict:
        v = rate["vol"]
        fga, fta = v["fga"] * minutes, v["fta"] * minutes
        fg3a = fga * rate["share"]
        fg3m = fg3a * rate["p3"]
        fg2m = (fga - fg3a) * rate["p2"]
        ftm = fta * rate["pft"]
        d = {"min": minutes, "fga": fga, "fta": fta, "fg3a": fg3a, "fg3m": fg3m, "fgm": fg2m + fg3m, "ftm": ftm, "pts": 2 * fg2m + 3 * fg3m + ftm,
             "oreb": v["oreb"] * minutes, "dreb": v["dreb"] * minutes, "ast": v["ast"] * minutes, "stl": v["stl"] * minutes,
             "blk": v["blk"] * minutes, "tov": v["tov"] * minutes, "pf": v["pf"] * minutes}
        d["reb"] = d["oreb"] + d["dreb"]
        return d


def nb_draw(rng, mu: np.ndarray, phi: float) -> np.ndarray:
    mu = np.maximum(mu, 1e-9)
    if phi < 1e-3:
        return rng.poisson(mu)
    r = 1.0 / phi
    p = r / (r + mu)
    return rng.negative_binomial(r, p)


# ----------------------------------------------------------------------------------------------------------
@dataclass
class TeamPrediction:
    game_id: int
    team_id: int
    as_of: str
    players: Dict[int, dict] = field(default_factory=dict)  # pid -> summary
    draws: Optional[np.ndarray] = None                       # (D, n_players, len(DRAW_STATS)) float
    order: List[int] = field(default_factory=list)


class LabModel:
    """The distributional champion candidate. `blend` mixes ridge minutes with the EWMA baseline."""

    def __init__(self, ds: Dataset, fb: FeatureBuilder, cache: FeatureCache, name: str = "lab_v1", blend: float = 1.0, n_draws: int = 300):
        self.ds, self.fb, self.cache = ds, fb, cache
        self.name, self.blend, self.n_draws = name, blend, n_draws
        self.minutes = MinutesModel()
        self.play = PlayModel()
        self.rate = RateModel()
        self.cutoff = None
        self.version = None
        self.train_info: dict = {}
        self.rate._h = fb.h

    def fit(self, cutoff_ord: int) -> dict:
        ds, fb = self.ds, self.fb
        idx = _actuals(ds)
        rows_all, y_all, played_all, dates = [], [], [], []
        rows_min, y_min, d_min = [], [], []
        for gid, g in ds.games.items():
            if g["status"] != "final":
                continue
            gd = __import__("datetime").date.fromisoformat(g["game_date"]).toordinal()
            if gd >= cutoff_ord:
                continue
            for t in (g["home_team_id"], g["away_team_id"]):
                for r in self.cache.get(gid, t):
                    if r["n_prior"] < 3:
                        continue
                    i = idx.get((gid, r["player_id"]))
                    mins = float(ds.pg["min"][i]) if i is not None else 0.0
                    rows_all.append(r)
                    played_all.append(mins > 0)
                    if mins > 0 and r["status"] != "out":
                        rows_min.append(r)
                        y_min.append(mins)
                        d_min.append(gd)
        info = self.minutes.fit(rows_min, np.array(y_min), np.array(d_min))
        self.play.fit(rows_all, np.array(played_all))
        self.rate.fit(ds, fb, cutoff_ord)
        self.cutoff = cutoff_ord
        last = __import__("datetime").date.fromordinal(cutoff_ord - 1).isoformat()
        self.version = f"{self.name}@{last}"
        self.train_info = {**info, "cutoff": cutoff_ord, "rows_play_model": len(rows_all), "phi": {k: round(v, 4) for k, v in self.rate.phi.items()},
                           "p_ot": [round(float(x), 4) for x in self.rate.ots], "play_table": {f"{k[0]}|{k[1]}": v for k, v in self.play.table.items()}}
        return self.train_info

    # ---------------------------------------------------------------------------------------------------
    def minutes_if_play(self, feat: dict) -> float:
        ridge = self.minutes.predict(feat)
        if self.blend < 1.0:
            ew = feat["ewm_min"] * feat["restr_ratio"]
            ridge = self.blend * ridge + (1 - self.blend) * ew
        if feat["n_prior"] == 0:
            ridge = 10.0
        if feat.get("restriction_min"):
            ridge = min(ridge, feat["restriction_min"] * 1.05)
        return float(np.clip(ridge, 1.0, 44.0))

    def predict_team(self, game_id: int, team_id: int, as_of: Optional[str] = None, n_draws: Optional[int] = None, seed_extra: str = "") -> TeamPrediction:
        ds = self.ds
        g = ds.games[game_id]
        feats = self.cache.get(game_id, team_id, as_of)
        as_of = feats[0]["as_of"] if feats else (as_of or "")
        feats = sorted(feats, key=lambda f: f["player_id"])
        D = n_draws or self.n_draws
        import zlib as _z
        rng = np.random.default_rng(_z.crc32(f"{self.version}|{game_id}|{team_id}|{as_of}|{seed_extra}".encode()))
        n = len(feats)
        p_play = np.array([self.play.p(f) for f in feats])
        mu = np.array([self.minutes_if_play(f) for f in feats])
        raw = np.zeros((D, n))
        for j, f in enumerate(feats):
            pool = self.minutes.pools[_bucket(mu[j])]
            r = pool[rng.integers(0, len(pool), D)]
            m = np.clip(mu[j] + r, 1.0, 46.0)
            if f.get("restriction_min"):
                m = np.minimum(m, f["restriction_min"] * 1.25)
            raw[:, j] = m
        play = rng.random((D, n)) < p_play[None, :]
        raw = raw * play
        ot = rng.choice(3, size=D, p=self.rate.ots)
        T = 240.0 + 25.0 * ot
        tot = raw.sum(axis=1)
        mins = raw.copy()
        ok = tot > 0
        mins[ok] = raw[ok] / tot[ok, None] * T[ok, None]
        for _ in range(6):  # cap at 46 and redistribute
            over = mins > 46.0
            if not over.any():
                break
            excess = np.where(over, mins - 46.0, 0).sum(axis=1)
            mins = np.minimum(mins, 46.0)
            room = np.where((mins < 46.0) & (mins > 0), mins, 0.0)
            rs = room.sum(axis=1)
            add = np.where(rs[:, None] > 0, room / np.where(rs[:, None] > 0, rs[:, None], 1) * excess[:, None], 0)
            mins = mins + add
        draws = np.zeros((D, n, len(DRAW_STATS)))
        ix = {s: i for i, s in enumerate(DRAW_STATS)}
        d_ord = feats[0]["date_ord"] if feats else 0
        rate_cache = []
        for j, f in enumerate(feats):
            rt = self.rate.rates(f["player_id"], d_ord, f, ds, self.fb)
            rate_cache.append(rt)
            m = mins[:, j]
            fga = nb_draw(rng, rt["vol"]["fga"] * m, self.rate.phi["fga"])
            fta = nb_draw(rng, rt["vol"]["fta"] * m, self.rate.phi["fta"])
            fg3a = rng.binomial(fga, rt["share"])
            fg3m = rng.binomial(fg3a, rt["p3"])
            fg2m = rng.binomial(fga - fg3a, rt["p2"])
            ftm = rng.binomial(fta, rt["pft"])
            oreb = nb_draw(rng, rt["vol"]["oreb"] * m, self.rate.phi["oreb"])
            dreb = nb_draw(rng, rt["vol"]["dreb"] * m, self.rate.phi["dreb"])
            draws[:, j, ix["min"]] = m
            draws[:, j, ix["fga"]], draws[:, j, ix["fta"]] = fga, fta
            draws[:, j, ix["fg3a"]], draws[:, j, ix["fg3m"]], draws[:, j, ix["ftm"]] = fg3a, fg3m, ftm
            draws[:, j, ix["fgm"]] = fg2m + fg3m
            draws[:, j, ix["pts"]] = 2 * fg2m + 3 * fg3m + ftm
            draws[:, j, ix["oreb"]], draws[:, j, ix["dreb"]], draws[:, j, ix["reb"]] = oreb, dreb, oreb + dreb
            for s in ("ast", "stl", "blk", "tov"):
                draws[:, j, ix[s]] = nb_draw(rng, rt["vol"][s] * m, self.rate.phi[s])
            draws[:, j, ix["pf"]] = np.minimum(nb_draw(rng, rt["vol"]["pf"] * m, self.rate.phi["pf"]), 6)
        tp = TeamPrediction(game_id, team_id, as_of, {}, draws, [f["player_id"] for f in feats])
        for j, f in enumerate(feats):
            tp.players[f["player_id"]] = self._summary(f, p_play[j], mu[j], draws[:, j, :], rate_cache[j])
        return tp

    # ---------------------------------------------------------------------------------------------------
    def _summary(self, f: dict, p_play: float, mu: float, dr: np.ndarray, rt: dict) -> dict:
        ix = {s: i for i, s in enumerate(DRAW_STATS)}
        m = dr[:, ix["min"]]
        stats = {}
        for s in DRAW_STATS:
            v = dr[:, ix[s]]
            stats[s] = {"mean": float(v.mean()), "sd": float(v.std()), "p10": float(np.percentile(v, 10)), "p50": float(np.percentile(v, 50)), "p90": float(np.percentile(v, 90))}
        cats = np.stack([dr[:, ix[c]] >= 10 for c in ("pts", "reb", "ast", "stl", "blk")]).sum(axis=0)
        stats["dd_prob"] = float((cats >= 2).mean())
        stats["td_prob"] = float((cats >= 3).mean())
        played = m > 0
        return {"player_id": f["player_id"], "p_play": float(p_play), "min_if_play": float(mu), "min_mean": float(m.mean()),
                "min_p10": float(np.percentile(m, 10)), "min_p50": float(np.percentile(m, 50)), "min_p90": float(np.percentile(m, 90)),
                "stats": stats, "status": f["status"], "n_prior": f["n_prior"],
                "pace_mult": rt["pace_mult"], "eff_mult": rt["eff_mult"], "restriction_min": f.get("restriction_min")}

    def explain(self, feat: dict, summary: dict) -> dict:
        ds = self.ds
        pid = feat["player_id"]
        contrib = self.minutes.contributions(feat)
        flags = []
        if feat["n_prior"] < 10:
            flags.append(f"Low sample: only {int(feat['n_prior'])} prior games; rates lean heavily on position priors.")
        if feat["status"]:
            src = feat.get("status_source") or "unknown source"
            flags.append(f"Published status '{feat['status']}' ({feat.get('status_detail')}) from {src} at {feat.get('status_published_at')}.")
            try:
                from datetime import datetime as _dt
                age = (_dt.strptime(feat["as_of"][:19], "%Y-%m-%dT%H:%M:%S") - _dt.strptime(feat["status_published_at"][:19], "%Y-%m-%dT%H:%M:%S")).total_seconds() / 3600
                if age >= 24:
                    flags.append(f"STALE INPUT: that report is {age:.0f} hours old relative to this projection's as-of time; treat with caution.")
            except Exception:
                pass
        if feat["restr_flag"]:
            flags.append(f"Minutes restriction published: about {feat['restriction_min']} minutes.")
        return {"minutes_drivers": contrib, "baseline_minutes": round(self.minutes.intercept, 2),
                "p_play": round(summary["p_play"], 3), "min_if_play": round(summary["min_if_play"], 2),
                "pace_multiplier": round(summary["pace_mult"], 3), "matchup_efficiency_multiplier": round(summary["eff_mult"], 3),
                "flags": flags, "history_games": int(feat["n_prior"]), "model_version": self.version}


def pack_draws(draws_pj: np.ndarray) -> bytes:
    """(D, stats) float -> compressed uint8 (minutes stored x5, others clipped to 255)."""
    a = draws_pj.copy()
    a[:, 0] = np.round(a[:, 0] * MIN_SCALE)
    return zlib.compress(np.clip(np.round(a), 0, 255).astype(np.uint8).tobytes(), 6)


def unpack_draws(blob: bytes) -> np.ndarray:
    a = np.frombuffer(zlib.decompress(blob), dtype=np.uint8).reshape(-1, len(DRAW_STATS)).astype(float)
    a[:, 0] /= MIN_SCALE
    return a


def fantasy_summary(draws_pj: np.ndarray, rules: Dict[str, float]) -> dict:
    st = {s: draws_pj[:, i] for i, s in enumerate(DRAW_STATS)}
    f = score(st, rules)
    q = np.percentile(f, [10, 25, 50, 75, 90])
    mean = float(f.mean())
    sd = float(f.std())
    return {"mean": mean, "sd": sd, "p10": float(q[0]), "p25": float(q[1]), "p50": float(q[2]), "p75": float(q[3]), "p90": float(q[4]),
            "consistency": float(1.0 - min(sd / mean, 1.0)) if mean > 0 else 0.0}
