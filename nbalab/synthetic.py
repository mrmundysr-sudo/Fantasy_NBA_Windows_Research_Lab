"""Deterministic SYNTHETIC league generator.

Everything produced here is fictional: invented teams, invented player names, invented
seasons ("SYN-1", "SYN-2") and calendar dates in the 2030s. It exists so the whole lab
(registry, engine, query inspector, models, event handling, audits) can be exercised and
tested end to end without real data. It must never be presented as real NBA data and
projections built from it are never real picks.

The generative story has real structure so that modelling code has something true to find:
minutes depend on role, rest, coach, blowouts, injuries/returns and teammates being out;
box-score stats depend on minutes, usage, pace, opponent quality and home court.
Latent ground truth is stored in `synthetic_truth` for validation only; models never read it.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from typing import Dict, List

import numpy as np

from .store import Store

LABEL = "SYNTHETIC"
SEASONS = ("SYN-1", "SYN-2")
SEASON_START = {"SYN-1": date(2031, 10, 21), "SYN-2": date(2032, 10, 19)}
POSITIONS = ["PG", "SG", "SF", "PF", "C"]

_CITIES = ["Harbor City", "Summit", "Red Mesa", "Lakeshore", "Cinder Falls", "Bayfront", "Iron Valley", "Pinecrest", "Sundial",
           "Northgate", "Copper Bay", "Glass Harbor", "Stonebridge", "Westfield", "Marlow", "Ash Ridge", "Delta Springs",
           "Fox Hollow", "Granite Point", "Highmoor", "Ivory Coast", "Juniper", "Kestrel", "Lumen", "Maple Run",
           "Nova Park", "Orchard", "Prairie Gate", "Quarry Hill", "Riverbend"]
_MASCOTS = ["Tide", "Peaks", "Foxes", "Waves", "Embers", "Anchors", "Forge", "Pines", "Comets", "Wolves", "Miners", "Herons",
            "Bridges", "Drifters", "Bison", "Cedars", "Rapids", "Hawks", "Tigers", "Sparks", "Lancers", "Orcas", "Falcons",
            "Lanterns", "Rooks", "Meteors", "Harvest", "Stampede", "Quakes", "Ferries"]
_SYL_A = ["Dra", "Ke", "Tav", "Mor", "Zel", "Ori", "Bran", "Jax", "Lu", "Ny", "Vor", "Sef", "Kai", "Rem", "Tor", "Az", "Pel", "Gri"]
_SYL_B = ["vik", "ron", "ian", "dell", "mar", "sen", "tos", "nik", "lan", "dor", "quin", "ven", "rik", "bas", "mir", "sol"]
_SYL_C = ["Sol", "Kess", "Vand", "Ork", "Hal", "Brem", "Tull", "Marr", "Quill", "Dray", "Ness", "Voss", "Pell", "Crow", "Ames", "Ryn"]
_SYL_D = ["mar", "ler", "ford", "wick", "stone", "berg", "ton", "hart", "man", "field", "gate", "son", "worth", "den"]


def _name(rng, used) -> str:
    for _ in range(100):
        n = f"{rng.choice(_SYL_A)}{rng.choice(_SYL_B)} {rng.choice(_SYL_C)}{rng.choice(_SYL_D)}"
        if n not in used:
            used.add(n)
            return n
    raise RuntimeError("name pool exhausted")


def _haversine(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def generate(store: Store, seed: int = 7, days_s1: int = 90, days_s2: int = 60, upcoming_days: int = 8, n_teams: int = 30) -> dict:
    rng = np.random.default_rng(seed)
    used: set = set()
    teams, coaches, players = [], [], []
    team_lat, team_lon = {}, {}
    # ---- teams, coaches, players ---------------------------------------------------------------
    tstate: Dict[int, dict] = {}
    pstate: Dict[int, dict] = {}
    coach_seq = 0
    pid = 0
    for t in range(1, n_teams + 1):
        city, mascot = _CITIES[(t - 1) % len(_CITIES)], _MASCOTS[(t - 1) % len(_MASCOTS)]
        lat, lon = float(rng.uniform(26, 47)), float(rng.uniform(-123, -71))
        team_lat[t], team_lon[t] = lat, lon
        teams.append((t, f"S{t:02d}", f"{city} {mascot}", city, "East" if t % 2 else "West", lat, lon, LABEL))
        coach_seq += 1
        coaches.append((coach_seq, _name(rng, used), LABEL))
        tstate[t] = dict(off=float(rng.normal(0, 2.6)), de=float(rng.normal(0, 2.6)), pace=float(rng.normal(99, 2.4)),
                         coach=coach_seq, coach_pace=float(rng.normal(1, 0.015)), coach_depth=float(rng.normal(1, 0.045)),
                         last_date=None, last_city=t, roster=[])
        for slot in range(12):
            pid += 1
            pos = POSITIONS[(slot + int(rng.integers(0, 5))) % 5] if slot >= 5 else POSITIONS[slot]
            if slot < 5:
                base = float(np.clip(rng.normal(31.5, 2.8), 25, 37))
            elif slot < 9:
                base = float(np.clip(rng.normal(19.5, 3.5), 11, 26))
            elif slot < 11:
                base = float(np.clip(rng.normal(8, 3), 2.5, 14))
            else:
                base = float(np.clip(rng.normal(3.5, 1.5), 0.5, 7))
            star = slot == int(rng.integers(0, 3)) and slot < 3
            big = pos in ("PF", "C")
            guard = pos in ("PG", "SG")
            age = int(np.clip(rng.normal(26.5, 3.8), 19, 38))
            p = dict(
                base_min=base, usage=float(np.clip(rng.normal(1.0, 0.26) * (1.8 if star else 1.0), 0.4, 2.8)),
                ftr=float(np.clip(rng.normal(0.26, 0.08), 0.08, 0.6)), tov_rate=float(np.clip(rng.normal(0.115, 0.03), 0.04, 0.22)),
                three_share=float(np.clip(rng.normal(0.44 if guard else (0.34 if pos == "SF" else 0.14), 0.09), 0.0, 0.7)),
                p2=float(np.clip(rng.normal(0.52 + (0.05 if big else 0), 0.035), 0.4, 0.68)),
                p3=float(np.clip(rng.normal(0.355, 0.028), 0.27, 0.45)),
                pft=float(np.clip(rng.normal(0.70 if big else 0.79, 0.07), 0.5, 0.93)),
                oreb_w={"C": 2.0, "PF": 1.4, "SF": 0.8, "SG": 0.55, "PG": 0.45}[pos] * float(rng.lognormal(0, 0.18)),
                dreb_w={"C": 1.9, "PF": 1.45, "SF": 0.95, "SG": 0.7, "PG": 0.62}[pos] * float(rng.lognormal(0, 0.16)),
                ast_w={"PG": 2.4, "SG": 1.15, "SF": 0.95, "PF": 0.75, "C": 0.65}[pos] * float(rng.lognormal(0, 0.25)),
                stl_w=float(rng.lognormal(0, 0.28)) * (1.1 if guard else 0.9), blk_w={"C": 2.4, "PF": 1.3, "SF": 0.6, "SG": 0.45, "PG": 0.35}[pos] * float(rng.lognormal(0, 0.25)),
                pf_rate=float(np.clip(rng.normal(3.3, 0.6), 1.8, 5.0)), drive_w=float(rng.lognormal(0, 0.3)) * (1.4 if guard else 0.8),
                cont_w=float(rng.lognormal(0, 0.25)), age=age, prone=float(rng.lognormal(0, 0.45)), pos=pos, slot=slot, star=bool(star),
            )
            pstate[pid] = dict(team=t, inj=0, ret=False, p=p, name=_name(rng, used))
            players.append((pid, pstate[pid]["name"], t, pos, age, int(rng.integers(72, 85) if big else rng.integers(73 if pos == "SF" else 70, 80)), LABEL))
            tstate[t]["roster"].append(pid)

    store.insert_many("teams", ["team_id", "abbr", "name", "city", "conference", "lat", "lon", "data_label"], teams)
    store.insert_many("coaches", ["coach_id", "name", "data_label"], coaches)
    store.insert_many("players", ["player_id", "name", "team_id", "position", "age", "height_in", "data_label"], players)

    games_rows, box_rows, avail_rows, event_rows = [], [], [], []
    game_id = 0
    last_played_date = None

    def schedule_day(day: date) -> List[tuple]:
        order = [int(x) for x in rng.permutation(n_teams) + 1]
        avail = []
        for t in order:
            ld = tstate[t]["last_date"]
            b2b = ld is not None and (day - ld).days == 1
            if rng.random() < (0.22 if b2b else 0.58):
                avail.append(t)
        if len(avail) % 2:
            avail.pop()
        return [(avail[i], avail[i + 1]) for i in range(0, len(avail), 2)]

    def pre_game_availability(t: int, day: date, b2b: bool, tip: datetime):
        """Decide who is out and publish pregame injury events. Returns (active ids, restriction map)."""
        active, restr = [], {}
        for p_id in tstate[t]["roster"]:
            ps = pstate[p_id]
            reason = None
            status_pub = None
            if ps["inj"] > 0:
                last = ps["inj"] == 1
                ps["inj"] -= 1
                if last and rng.random() < 0.5:
                    # game-time decision: ~55% play on a minutes restriction
                    if rng.random() < 0.55:
                        restr[p_id] = float(rng.uniform(0.5, 0.78))
                        status_pub = ("questionable", "returning from injury", restr[p_id])
                    else:
                        reason = "injury"
                        status_pub = ("questionable", "returning from injury", None)
                else:
                    reason = "injury"
                    status_pub = ("out", "injury", None)
            elif ps["ret"]:
                restr[p_id] = float(rng.uniform(0.6, 0.85))
                status_pub = ("probable", "returning from injury", restr[p_id])
            elif b2b and ps["p"]["age"] >= 30 and ps["p"]["slot"] < 7 and rng.random() < 0.07:
                reason = "rest"
                status_pub = ("out", "rest / load management", None)
            elif rng.random() < 0.008:
                reason = "illness"
                status_pub = ("out", "illness", None)
            elif ps["p"]["slot"] >= 10 and rng.random() < 0.40:
                reason = "dnp_cd"
            if status_pub:
                pub = tip - timedelta(hours=int(rng.integers(6, 30)))
                event_rows.append(("SYN_DEMO_FEED", "injury", t, p_id, None, status_pub[0], status_pub[1],
                                   (None if status_pub[2] is None else round(status_pub[2] * ps["p"]["base_min"], 1)),
                                   pub.strftime("%Y-%m-%dT%H:%M:%SZ"), pub.strftime("%Y-%m-%dT%H:%M:%SZ"), LABEL))
            if reason:
                avail_rows.append((None, t, p_id, "dnp_cd" if reason == "dnp_cd" else "out", reason))
                continue
            active.append(p_id)
        # make sure we always have at least 8 healthy bodies
        if len(active) < 8:
            for p_id in tstate[t]["roster"]:
                if p_id not in active:
                    active.append(p_id)
                    if len(active) >= 8:
                        break
        return active, restr

    def sim_team_game(t, opp, home, active, restr, ot, script, b2b, rest_days, T):
        ts, os_ = tstate[t], tstate[opp]
        coach_depth, n = ts["coach_depth"], len(active)
        # --- minutes ---
        order = sorted(active, key=lambda i: -pstate[i]["p"]["base_min"] * (0.6 if pstate[i]["ret"] and i in restr else 1.0))
        starters = set(order[:5])
        blow = float(np.clip((abs(script) - 12) / 14, 0, 1))
        w = np.zeros(n)
        for k, i in enumerate(active):
            p = pstate[i]["p"]
            wi = p["base_min"]
            if i in starters:
                wi *= coach_depth * (1 - 0.30 * blow)
                wi *= float(rng.lognormal(0, 0.075))
            else:
                wi *= (1 + 0.55 * blow) * float(rng.lognormal(0, 0.22))
            if b2b and p["age"] >= 31:
                wi *= 0.94
            elif rest_days >= 3:
                wi *= 1.015
            if i in restr:
                wi *= restr[i]
            # teammates out raise others' minutes via renormalisation (below); depth chart effect:
            w[k] = max(wi, 0.2)
        mins = w / w.sum() * T
        for _ in range(6):  # cap individual minutes (not in OT)
            cap = 41.5 if ot == 0 else 48.0
            over = mins > cap
            if not over.any():
                break
            excess = (mins[over] - cap).sum()
            mins[over] = cap
            room = ~over
            if room.any():
                mins[room] += excess * mins[room] / mins[room].sum()
        mins = np.round(mins, 1)
        mins[np.argmax(mins)] += round(T - mins.sum(), 1)
        mins = np.maximum(mins, 0.0)
        # --- possessions & usage ---
        return starters, mins

    def sim_game(season, day, h, a, tip):
        nonlocal game_id, last_played_date
        game_id += 1
        gid = game_id
        ot = int(rng.random() < 0.055)
        if ot and rng.random() < 0.15:
            ot = 2
        T = 240.0 + 25.0 * ot
        res = {}
        script = float(rng.normal(0, 12.5)) + (tstate[h]["off"] - tstate[h]["de"] - tstate[a]["off"] + tstate[a]["de"]) * 0.9 + 2.2
        b2b = {h: tstate[h]["last_date"] is not None and (day - tstate[h]["last_date"]).days == 1,
               a: tstate[a]["last_date"] is not None and (day - tstate[a]["last_date"]).days == 1}
        rest = {t: (999 if tstate[t]["last_date"] is None else (day - tstate[t]["last_date"]).days) for t in (h, a)}
        avail_before = len(avail_rows)
        ev_before = len(event_rows)
        a_h, r_h = pre_game_availability(h, day, b2b[h], tip)
        a_a, r_a = pre_game_availability(a, day, b2b[a], tip)
        for k in range(avail_before, len(avail_rows)):
            r = avail_rows[k]
            avail_rows[k] = (gid,) + r[1:]
        for e_idx in range(ev_before, len(event_rows)):
            e = event_rows[e_idx]
            event_rows[e_idx] = (e[0], e[1], e[2], e[3], gid) + e[5:]
        pace = 0.5 * (tstate[h]["pace"] * tstate[h]["coach_pace"] + tstate[a]["pace"] * tstate[a]["coach_pace"])
        poss0 = pace * (T / 240.0) * float(rng.normal(1.0, 0.03))
        out = {}
        for t, opp, home, active, restr, sgn in ((h, a, 1, a_h, r_h, 1), (a, h, 0, a_a, r_a, -1)):
            starters, mins = sim_team_game(t, opp, home, active, restr, ot, script * sgn, b2b[t], rest[t], T)
            n = len(active)
            ts, os_ = tstate[t], tstate[opp]
            eff = (ts["off"] - os_["de"]) / 100.0 + (0.012 if home else 0.0) + sgn * script / 1500.0
            plays_total = poss0 + 11.0 * (T / 240.0)
            usage_w = np.array([pstate[i]["p"]["usage"] for i in active]) * mins
            plays = plays_total * usage_w / usage_w.sum()
            fga = np.zeros(n); fta = np.zeros(n); tov = np.zeros(n)
            fg3a = np.zeros(n); fg3m = np.zeros(n); fgm = np.zeros(n); ftm = np.zeros(n)
            for k, i in enumerate(active):
                p = pstate[i]["p"]
                pl = max(plays[k], 0.0)
                tv = pl * p["tov_rate"]
                fg_a = (pl - tv) / (1 + 0.44 * p["ftr"])
                tov[k] = rng.poisson(tv)
                fga[k] = rng.poisson(fg_a)
                fta[k] = rng.poisson(fg_a * p["ftr"])
                fg3a[k] = rng.binomial(int(fga[k]), p["three_share"])
                p3 = float(np.clip(p["p3"] * (1 + 0.9 * eff), 0.1, 0.6))
                p2 = float(np.clip(p["p2"] * (1 + 0.8 * eff), 0.25, 0.8))
                pft = float(np.clip(p["pft"] * (1 + 0.15 * eff), 0.3, 0.97))
                fg3m[k] = rng.binomial(int(fg3a[k]), p3)
                fg2m = rng.binomial(int(fga[k] - fg3a[k]), p2)
                fgm[k] = fg3m[k] + fg2m
                ftm[k] = rng.binomial(int(fta[k]), pft)
            pts = 2 * (fgm - fg3m) + 3 * fg3m + ftm
            out[t] = dict(active=active, mins=mins, starters=starters, fga=fga, fta=fta, tov=tov, fg3a=fg3a, fg3m=fg3m, fgm=fgm,
                          ftm=ftm, pts=pts, opp=opp, home=home, eff=eff, restr=restr)
        # ties broken by a late free-throw pair (never leaves a tie)
        th, ta = out[h]["pts"].sum(), out[a]["pts"].sum()
        if th == ta:
            side = h if rng.random() < 0.5 else a
            k = int(np.argmax(out[side]["mins"]))
            out[side]["fta"][k] += 2; out[side]["ftm"][k] += 2; out[side]["pts"][k] += 2
        margin_h = int(out[h]["pts"].sum() - out[a]["pts"].sum())
        # ---- second pass: rebounds, assists, defense, fouls, tracking, plus/minus ----
        for t in (h, a):
            o = out[t]
            opp = o["opp"]
            oo = out[opp]
            n = len(o["active"])
            mins = o["mins"]
            tm_misses = (o["fga"] - o["fgm"]).sum() + 0.44 * (o["fta"] - o["ftm"]).sum() * 0.5
            opp_misses = (oo["fga"] - oo["fgm"]).sum() + 0.44 * (oo["fta"] - oo["ftm"]).sum() * 0.5
            team_oreb = int(rng.binomial(int(max(tm_misses, 1)), 0.255))
            opp_oreb = int(rng.binomial(int(max(opp_misses, 1)), 0.255))
            team_dreb = int(max(opp_misses - opp_oreb, 0))
            ow = np.array([pstate[i]["p"]["oreb_w"] for i in o["active"]]) * mins
            dw = np.array([pstate[i]["p"]["dreb_w"] for i in o["active"]]) * mins
            oreb = rng.multinomial(team_oreb, ow / ow.sum())
            dreb = rng.multinomial(team_dreb, dw / dw.sum())
            tm_fgm = o["fgm"].sum()
            team_ast = int(rng.binomial(int(tm_fgm), float(np.clip(0.60 + 0.02 * o["eff"] * 100, 0.4, 0.78))))
            aw = np.array([pstate[i]["p"]["ast_w"] for i in o["active"]]) * mins
            ast = rng.multinomial(team_ast, aw / aw.sum())
            ast = np.minimum(ast, np.maximum(tm_fgm - o["fgm"], 0)).astype(int)
            opp_poss_est = oo["fga"].sum() + 0.44 * oo["fta"].sum() - opp_oreb + oo["tov"].sum()
            sw = np.array([pstate[i]["p"]["stl_w"] for i in o["active"]]) * mins
            team_stl = int(rng.poisson(max(opp_poss_est, 1) * 0.074 * (1 + 0.006 * tstate[t]["de"] * -1)))
            stl = rng.multinomial(team_stl, sw / sw.sum())
            bw = np.array([pstate[i]["p"]["blk_w"] for i in o["active"]]) * mins
            opp_two_att = (oo["fga"] - oo["fg3a"]).sum()
            team_blk = int(rng.poisson(max(opp_two_att, 1) * 0.062))
            blk = rng.multinomial(team_blk, bw / bw.sum())
            pf = np.minimum(rng.poisson(np.array([pstate[i]["p"]["pf_rate"] for i in o["active"]]) * mins / 36.0), 6)
            touches = rng.poisson(np.maximum(o["fga"] + o["tov"] + ast * 2.2 + 18 * mins / 36 * np.array([pstate[i]["p"]["ast_w"] for i in o["active"]]) * 0.4, 1))
            drives = rng.poisson(np.array([pstate[i]["p"]["drive_w"] for i in o["active"]]) * mins / 36 * 6.0)
            pot_ast = np.maximum(ast + rng.poisson(ast * 0.9 + 0.3), 0)
            contested = rng.poisson(np.array([pstate[i]["p"]["cont_w"] for i in o["active"]]) * mins / 36 * 5.0)
            margin = margin_h if t == h else -margin_h
            raw_e = rng.normal(0, 3.2, n) * np.sqrt(np.maximum(mins, 1) / 30.0)
            on_share = mins / (mins.sum() / 5.0)
            base_pm = margin * on_share
            e = raw_e - np.average(raw_e, weights=on_share) if on_share.sum() else raw_e
            pm = np.round(base_pm + e).astype(int)
            target = 5 * margin
            diff = int(target - pm.sum())
            pm[int(np.argmax(mins))] += diff  # keep sum(pm) == 5 * team margin exactly
            o.update(oreb=oreb, dreb=dreb, ast=ast, stl=stl, blk=blk, pf=pf, touches=touches, drives=drives, pot_ast=pot_ast,
                     contested=contested, pm=pm)
        # ---- rows ----
        for t in (h, a):
            o = out[t]
            for k, i in enumerate(o["active"]):
                if o["mins"][k] <= 0.05:
                    continue
                reb = int(o["oreb"][k] + o["dreb"][k])
                box_rows.append((gid, i, t, int(i in o["starters"]), float(o["mins"][k]), int(o["pts"][k]), int(o["fgm"][k]), int(o["fga"][k]),
                                 int(o["fg3m"][k]), int(o["fg3a"][k]), int(o["ftm"][k]), int(o["fta"][k]), int(o["oreb"][k]), int(o["dreb"][k]),
                                 reb, int(o["ast"][k]), int(o["stl"][k]), int(o["blk"][k]), int(o["tov"][k]), int(o["pf"][k]), int(o["pm"][k]),
                                 int(o["touches"][k]), int(o["drives"][k]), int(o["pot_ast"][k]), int(o["contested"][k])))
        games_rows.append((gid, season, day.isoformat(), h, a, tstate[h]["coach"], tstate[a]["coach"], "final",
                           int(out[h]["pts"].sum()), int(out[a]["pts"].sum()), ot, LABEL))
        # ---- post-game: injuries, return flags ----
        for t in (h, a):
            o = out[t]
            for k, i in enumerate(o["active"]):
                ps = pstate[i]
                if ps["ret"] and o["mins"][k] > 0:
                    ps["ret"] = False
                m = float(o["mins"][k])
                if m > 0 and rng.random() < 0.0085 * (m / 30.0) * ps["p"]["prone"]:
                    ps["inj"] = int(1 + rng.geometric(1 / 5.0))
                    ps["ret"] = True
                    pub = datetime(day.year, day.month, day.day, 23, 30)
                    event_rows.append(("SYN_DEMO_FEED", "injury", t, i, gid, "out", "in-game injury", None,
                                       pub.strftime("%Y-%m-%dT%H:%M:%SZ"), pub.strftime("%Y-%m-%dT%H:%M:%SZ"), LABEL))
            tstate[t]["last_date"] = day
        last_played_date = day

    # ---- run seasons ------------------------------------------------------------------------------
    def run_season(season: str, ndays: int, drift: bool):
        nonlocal last_played_date
        start = SEASON_START[season]
        for t in tstate.values():
            t["last_date"] = None
        for pstate_i in pstate.values():
            pstate_i["inj"], pstate_i["ret"] = 0, False
            pstate_i["p"]["age"] += 1 if drift else 0
        if drift:
            for t in tstate.values():
                t["off"] = 0.7 * t["off"] + float(rng.normal(0, 1.8)); t["de"] = 0.7 * t["de"] + float(rng.normal(0, 1.8))
        coach_changes = {}
        if season == "SYN-1":
            for tt in rng.choice(np.arange(1, n_teams + 1), size=5, replace=False):
                coach_changes[int(tt)] = int(ndays * rng.uniform(0.45, 0.65))
        for d in range(ndays):
            day = start + timedelta(days=d)
            for tt, when in coach_changes.items():
                if d == when:
                    nonlocal_coach = len(coaches) + 1
                    coaches.append((nonlocal_coach, _name(rng, used), LABEL))
                    tstate[tt]["coach"] = nonlocal_coach
                    tstate[tt]["coach_pace"] = float(rng.normal(1, 0.02)); tstate[tt]["coach_depth"] = float(rng.normal(1, 0.05))
            for (h, a) in schedule_day(day):
                tip = datetime(day.year, day.month, day.day, 19, 30)
                sim_game(season, day, h, a, tip)

    run_season("SYN-1", days_s1, drift=False)
    run_season("SYN-2", days_s2, drift=True)
    # ---- upcoming (scheduled, not simulated) games ----------------------------------------------------
    start2 = SEASON_START["SYN-2"]
    first_up = start2 + timedelta(days=days_s2)
    upcoming = []
    for d in range(upcoming_days):
        day = first_up + timedelta(days=d)
        for (h, a) in schedule_day(day):
            game_id += 1
            upcoming.append((game_id, "SYN-2", day.isoformat(), h, a, tstate[h]["coach"], tstate[a]["coach"], "scheduled", None, None, 0, LABEL))
            tstate[h]["last_date"] = day; tstate[a]["last_date"] = day
    games_rows.extend(upcoming)

    store.insert_many("coaches", ["coach_id", "name", "data_label"], coaches)
    store.insert_many("games", ["game_id", "season", "game_date", "home_team_id", "away_team_id", "home_coach_id", "away_coach_id",
                                "status", "home_score", "away_score", "ot", "data_label"], games_rows)
    store.insert_many("box", ["game_id", "player_id", "team_id", "started", "min", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
                              "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf", "pm", "touches", "drives", "pot_ast", "contested"], box_rows)
    store.insert_many("availability", ["game_id", "team_id", "player_id", "status", "reason"], avail_rows)
    store.insert_many("events", ["source", "kind", "team_id", "player_id", "game_id", "status", "detail", "restriction_min",
                                 "published_at", "retrieved_at", "data_label"], event_rows)
    store.insert_many("synthetic_truth", ["player_id", "params_json"],
                      [(i, json.dumps({k: v for k, v in s["p"].items() if k not in ("pos",)}, default=float)) for i, s in pstate.items()])
    as_of = datetime.combine(first_up, datetime.min.time()).replace(hour=9)
    store.set_meta("data_label", LABEL)
    store.set_meta("synthetic", True)
    store.set_meta("seed", seed)
    store.set_meta("demo_as_of", as_of.strftime("%Y-%m-%dT%H:%M:%SZ"))
    store.set_meta("dataset_note", "SYNTHETIC FIXTURE: fictional teams/players/seasons. Not real NBA data. Projections are demonstrations only.")
    return dict(games=len(games_rows), box_rows=len(box_rows), events=len(event_rows), upcoming=len(upcoming), as_of=as_of.isoformat())
