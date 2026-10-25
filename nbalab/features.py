"""Leakage-safe pregame feature construction.

Contract: every feature for a target (player, game) is computed ONLY from
  * box-score rows with game date strictly BEFORE the target game's date,
  * schedule information (dates, home/away, opponent) which is known in advance,
  * events whose `published_at` is at or before the supplied `as_of` timestamp.
Nothing from the target game or later is read. tests/test_leakage.py enforces this by
corrupting all rows at or after a target date and checking features do not change.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import numpy as np

from .dataset import Dataset
from .store import Store

RATE_STATS = ["fga", "fg3a", "fg3m", "fgm", "fta", "ftm", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf"]
POSITIONS = ["PG", "SG", "SF", "PF", "C"]
MIN_FEATURES = ["ewm_min", "mean5", "mean_all", "start_ewm", "log_n_prior", "g_since", "returning", "rest_days", "b2b", "home", "age", "age_b2b",
                "pos_PG", "pos_SG", "pos_SF", "pos_PF", "pos_C", "abs_spread", "vacated_min", "vacated_starters", "restr_ratio", "restr_flag",
                "coach_starter_min", "depth_rank", "top5", "ewm_x_restr"]
A_MIN, DECAY_RATE = 0.25, 0.97
TIP_HOUR_ASOF = 18  # historical as_of = game date 18:00 (synthetic tip is 19:30)


def _ord(d: str) -> int:
    return date.fromisoformat(d[:10]).toordinal()


def asof_for_game(game_date: str) -> str:
    return f"{game_date}T{TIP_HOUR_ASOF:02d}:00:00Z"


class EventIndex:
    """Pregame status reports keyed by game, filtered by publication time."""

    def __init__(self, rows):
        self.by_game: Dict[int, List[dict]] = defaultdict(list)
        for r in rows:
            r = dict(r)
            if r.get("game_id") is not None and r.get("kind") == "injury":
                self.by_game[int(r["game_id"])].append(r)
        for lst in self.by_game.values():
            lst.sort(key=lambda r: (r["published_at"] or "", r["event_id"]))

    def status_asof(self, game_id: int, as_of: str) -> Dict[int, dict]:
        out: Dict[int, dict] = {}
        for r in self.by_game.get(game_id, []):
            if (r["published_at"] or "") <= as_of and r.get("player_id") is not None and not r.get("superseded_by"):
                out[int(r["player_id"])] = r
        return out


@dataclass
class PlayerState:
    n_prior: int = 0
    ewm_min: float = 0.0
    mean5: float = 0.0
    mean_all: float = 0.0
    start_ewm: float = 0.0
    dec_min: float = 0.0
    dec: Optional[np.ndarray] = None
    last_seq: int = -1
    last_date: int = 0
    cum_min: float = 0.0
    cum: Optional[np.ndarray] = None
    last_min: float = 0.0


class History:
    """Chronological per-player / per-team / per-coach states, queried strictly-before a date."""

    def __init__(self, ds: Dataset, store: Store):
        self.ds = ds
        pg = ds.pg
        self.sched: Dict[int, List[Tuple[int, int]]] = defaultdict(list)  # team -> [(date, game_id)]
        for gid, g in ds.games.items():
            for t in (g["home_team_id"], g["away_team_id"]):
                self.sched[t].append((_ord(g["game_date"]), gid))
        self.seq: Dict[Tuple[int, int], int] = {}
        for t, lst in self.sched.items():
            lst.sort()
            for k, (d, gid) in enumerate(lst):
                self.seq[(t, gid)] = k
        # --- player states ---
        order = np.lexsort((pg["game_id"], pg["date"], pg["player_id"]))
        self.p_dates: Dict[int, np.ndarray] = {}
        self.p_states: Dict[int, List[PlayerState]] = {}
        k_stats = len(RATE_STATS)
        cols = {c: pg[c] for c in RATE_STATS}
        i = 0
        n = len(order)
        while i < n:
            pid = int(pg["player_id"][order[i]])
            j = i
            while j < n and int(pg["player_id"][order[j]]) == pid:
                j += 1
            ds_, sts = [], []
            ewm = start = dec_min = 0.0
            dec = np.zeros(k_stats)
            cum = np.zeros(k_stats)
            cum_min = 0.0
            mins: List[float] = []
            for r in order[i:j]:
                m = float(pg["min"][r])
                if m <= 0:
                    continue
                ewm = m if not mins else (1 - A_MIN) * ewm + A_MIN * m
                start = float(pg["started"][r]) if not mins else (1 - A_MIN) * start + A_MIN * float(pg["started"][r])
                mins.append(m)
                dec_min = DECAY_RATE * dec_min + m
                row_vals = np.array([cols[c][r] for c in RATE_STATS])
                dec = DECAY_RATE * dec + row_vals
                cum = cum + row_vals
                cum_min += m
                tid = int(pg["team_id"][r])
                sts.append(PlayerState(len(mins), ewm, float(np.mean(mins[-5:])), float(np.mean(mins)), start, dec_min, dec.copy(),
                                       self.seq.get((tid, int(pg["game_id"][r])), -1), int(pg["date"][r]), cum_min, cum.copy(), m))
                ds_.append(int(pg["date"][r]))
            self.p_dates[pid] = np.array(ds_, dtype=np.int64)
            self.p_states[pid] = sts
            i = j
        # --- team states (margin, off, def, pace) after each game ---
        tg = ds.tg
        order = np.lexsort((tg["game_id"], tg["date"], tg["team_id"]))
        self.t_dates: Dict[int, np.ndarray] = {}
        self.t_states: Dict[int, List[Tuple[float, float, float, float]]] = {}
        i, n = 0, len(order)
        while i < n:
            tid = int(tg["team_id"][order[i]])
            j = i
            while j < n and int(tg["team_id"][order[j]]) == tid:
                j += 1
            margin = off = de = pace = 0.0
            ds_, sts = [], []
            first = True
            for r in order[i:j]:
                poss = float(tg["tm_poss"][r])
                o = 100 * float(tg["pts"][r]) / poss
                d_ = 100 * float(tg["opp_pts"][r]) / poss
                mg = float(tg["pts"][r] - tg["opp_pts"][r])
                pc = 48 * poss / (float(tg["min"][r]) / 5)
                if first:
                    margin, off, de, pace = mg, o, d_, pc
                    first = False
                else:
                    margin, off, de, pace = 0.92 * margin + 0.08 * mg, 0.93 * off + 0.07 * o, 0.93 * de + 0.07 * d_, 0.9 * pace + 0.1 * pc
                sts.append((margin, off, de, pace))
                ds_.append(int(tg["date"][r]))
            self.t_dates[tid] = np.array(ds_, dtype=np.int64)
            self.t_states[tid] = sts
            i = j
        # --- league cumulative points per 100 poss by date ---
        o = np.argsort(tg["date"], kind="stable")
        self.l_dates = tg["date"][o]
        self.l_cum_pts = np.cumsum(tg["pts"][o])
        self.l_cum_poss = np.cumsum(tg["tm_poss"][o])
        # --- coach starter-load state ---
        starters = pg["started"] > 0
        key = {}
        for r in np.nonzero(starters)[0]:
            key.setdefault((int(pg["coach_id"][r]), int(pg["game_id"][r])), []).append(float(pg["min"][r]))
        by_coach: Dict[int, List[Tuple[int, int, float]]] = defaultdict(list)
        for (cid, gid), v in key.items():
            by_coach[cid].append((_ord(ds.games[gid]["game_date"]), gid, float(np.mean(v))))
        self.c_dates: Dict[int, np.ndarray] = {}
        self.c_states: Dict[int, List[float]] = {}
        for cid, lst in by_coach.items():
            lst.sort()
            e, sts = None, []
            for _, _, v in lst:
                e = v if e is None else 0.9 * e + 0.1 * v
                sts.append(e)
            self.c_dates[cid] = np.array([d for d, _, _ in lst], dtype=np.int64)
            self.c_states[cid] = sts
        # league average rate per minute by position (all history; leakage-safe version built in model fit)
    # -------------------------------------------------------------------------------------------
    def player_state(self, pid: int, date_ord: int) -> PlayerState:
        d = self.p_dates.get(pid)
        if d is None or len(d) == 0:
            return PlayerState()
        k = int(np.searchsorted(d, date_ord, side="left"))
        return self.p_states[pid][k - 1] if k > 0 else PlayerState()

    def team_state(self, tid: int, date_ord: int) -> Tuple[float, float, float, float]:
        d = self.t_dates.get(tid)
        if d is None or len(d) == 0:
            return (0.0, 0.0, 0.0, 0.0)
        k = int(np.searchsorted(d, date_ord, side="left"))
        return self.t_states[tid][k - 1] if k > 0 else (0.0, 0.0, 0.0, 0.0)

    def league_ppp(self, date_ord: int) -> Tuple[float, float]:
        k = int(np.searchsorted(self.l_dates, date_ord, side="left"))
        if k == 0:
            return 110.0, 99.0
        return 100 * float(self.l_cum_pts[k - 1] / self.l_cum_poss[k - 1]), 99.0

    def coach_starter_min(self, cid: int, date_ord: int, default: float = 31.5) -> float:
        d = self.c_dates.get(cid)
        if d is None or len(d) == 0:
            return default
        k = int(np.searchsorted(d, date_ord, side="left"))
        return float(self.c_states[cid][k - 1]) if k > 0 else default

    def rest_info(self, tid: int, gid: int, date_ord: int) -> Tuple[int, int]:
        k = self.seq.get((tid, gid))
        if k is None or k == 0:
            return 7, 0
        prev_date = self.sched[tid][k - 1][0]
        rest = max(0, date_ord - prev_date - 1)
        return min(rest, 7), int(rest == 0)


class FeatureBuilder:
    def __init__(self, ds: Dataset, store: Store, history: Optional[History] = None):
        self.ds = ds
        self.store = store
        self.h = history or History(ds, store)
        self.events = EventIndex(store.query("SELECT * FROM events"))
        self.event_rev = 0
        self.roster: Dict[int, List[int]] = defaultdict(list)
        for pid, p in ds.players.items():
            self.roster[p["team_id"]].append(pid)

    def refresh_events(self) -> None:
        self.events = EventIndex(self.store.query("SELECT * FROM events"))
        self.event_rev += 1

    # -------------------------------------------------------------------------------------------
    def team_features(self, game_id: int, team_id: int, as_of: Optional[str] = None, events_override: Optional[Dict[int, dict]] = None) -> List[dict]:
        """One feature row per rostered player of `team_id` for `game_id` using information up to `as_of`."""
        ds, h = self.ds, self.h
        g = ds.games[game_id]
        d = _ord(g["game_date"])
        as_of = as_of or asof_for_game(g["game_date"])
        status = events_override if events_override is not None else self.events.status_asof(game_id, as_of)
        home = int(g["home_team_id"] == team_id)
        opp = g["away_team_id"] if home else g["home_team_id"]
        coach = g["home_coach_id"] if home else g["away_coach_id"]
        rest, b2b = h.rest_info(team_id, game_id, d)
        ts, os_ = h.team_state(team_id, d), h.team_state(opp, d)
        exp_margin = ts[0] - os_[0] + (2.2 if home else -2.2)
        coach_load = h.coach_starter_min(coach, d)
        rows = []
        states = {}
        for pid in self.roster[team_id]:
            states[pid] = h.player_state(pid, d)
        active_pool = []
        for pid, st in states.items():
            ev = status.get(pid)
            is_out = bool(ev and ev["status"] == "out")
            if st.n_prior > 0 and not is_out:
                active_pool.append((st.ewm_min, pid))
        ranked = {pid: r + 1 for r, (_, pid) in enumerate(sorted(active_pool, reverse=True))}
        vacated, vac_starters = 0.0, 0
        for pid, st in states.items():
            ev = status.get(pid)
            if ev and ev["status"] == "out" and st.n_prior > 0 and st.ewm_min >= 10:
                vacated += st.ewm_min
                vac_starters += int(st.start_ewm >= 0.5)
        for pid in self.roster[team_id]:
            st = states[pid]
            ev = status.get(pid)
            p = ds.players[pid]
            seq = h.seq.get((team_id, game_id), -1)
            g_since = min(max(seq - st.last_seq - 1, 0), 10) if st.n_prior > 0 and seq >= 0 else 0
            restr = ev["restriction_min"] if ev and ev.get("restriction_min") else None
            ewm = st.ewm_min if st.n_prior else 12.0
            restr_ratio = min(1.0, restr / ewm) if (restr and ewm > 0) else 1.0
            f = {
                "player_id": pid, "game_id": game_id, "team_id": team_id, "opp_team_id": opp, "coach_id": coach, "home": home,
                "ewm_min": ewm, "mean5": st.mean5 if st.n_prior else 12.0, "mean_all": st.mean_all if st.n_prior else 12.0,
                "start_ewm": st.start_ewm, "log_n_prior": float(np.log1p(st.n_prior)), "n_prior": st.n_prior, "g_since": float(g_since),
                "returning": float(g_since >= 1 and st.n_prior > 0), "rest_days": float(rest), "b2b": float(b2b), "age": float(p["age"]),
                "age_b2b": float(b2b * (p["age"] >= 31)),
                **{f"pos_{x}": float(p["position"] == x) for x in POSITIONS},
                "abs_spread": abs(exp_margin), "exp_margin": exp_margin,
                "vacated_min": (vacated - (st.ewm_min if (ev and ev["status"] == "out" and st.ewm_min >= 10) else 0.0)) / 10.0,
                "vacated_starters": float(vac_starters), "restr_ratio": restr_ratio, "restr_flag": float(restr is not None),
                "coach_starter_min": coach_load, "depth_rank": float(ranked.get(pid, 12)), "top5": float(ranked.get(pid, 12) <= 5),
                "ewm_x_restr": ewm * restr_ratio,
                "status": ev["status"] if ev else None, "status_detail": ev["detail"] if ev else None,
                "status_published_at": ev["published_at"] if ev else None, "status_source": ev["source"] if ev else None,
                "restriction_min": restr, "event_id": ev["event_id"] if ev else None,
                "team_off": ts[1], "team_def": ts[2], "team_pace": ts[3], "opp_off": os_[1], "opp_def": os_[2], "opp_pace": os_[3],
                "date_ord": d, "as_of": as_of,
            }
            rows.append(f)
        return rows

    def rate_state(self, pid: int, date_ord: int):
        st = self.h.player_state(pid, date_ord)
        return st
