"""Data-quality, leakage, immutability and reproducibility audits.

Every check returns {id, title, status: pass|fail|warn|info, detail}. The report is deterministic given the data
and seed so it can be diffed between runs ("reproducible audit").
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from typing import List

import numpy as np

from . import FORMULA_VERSION, __version__
from .features import FeatureBuilder, asof_for_game
from .store import Store


def _chk(cid, title, status, detail):
    return {"id": cid, "title": title, "status": status, "detail": detail}


def data_hash(store: Store) -> str:
    h = hashlib.sha256()
    for table, order in (("games", "game_id"), ("box", "game_id, player_id"), ("players", "player_id")):
        for r in store.query(f"SELECT * FROM {table} ORDER BY {order}"):
            h.update(repr(tuple(r)).encode())
    return h.hexdigest()


def data_quality(lab) -> List[dict]:
    ds, s = lab.ds, lab.store
    pg, tg = ds.pg, ds.tg
    out = []
    bad = int(((pg["pts"] != 2 * (pg["fgm"] - pg["fg3m"]) + 3 * pg["fg3m"] + pg["ftm"]) | (pg["reb"] != pg["oreb"] + pg["dreb"])).sum())
    out.append(_chk("identities", "Box-score identities (PTS = 2*FG2M + 3*FG3M + FTM; REB = OREB + DREB)", "pass" if bad == 0 else "fail", f"{bad} violating player-games of {len(pg['pts'])}"))
    bad = int(((pg["fg3m"] > pg["fgm"]) | (pg["fgm"] > pg["fga"]) | (pg["fg3m"] > pg["fg3a"]) | (pg["ftm"] > pg["fta"])).sum())
    out.append(_chk("makes_le_attempts", "Makes never exceed attempts", "pass" if bad == 0 else "fail", f"{bad} violations"))
    neg = int(sum((pg[c] < 0).sum() for c in ("min", "pts", "reb", "ast", "stl", "blk", "tov", "pf")))
    out.append(_chk("negatives", "No negative counting stats or minutes", "pass" if neg == 0 else "fail", f"{neg} negative values"))
    exp = 240 + 25 * tg["ot"]
    off = np.abs(tg["min"] - exp) > 0.06
    out.append(_chk("team_minutes", "Team minutes = 240 + 25 per overtime period", "pass" if not off.any() else "fail", f"{int(off.sum())} of {len(tg['min'])} team-games off"))
    dup = s.one("SELECT COUNT(*) c FROM (SELECT game_id, player_id FROM box GROUP BY 1,2 HAVING COUNT(*)>1)")["c"]
    out.append(_chk("duplicates", "No duplicate (game, player) rows", "pass" if dup == 0 else "fail", f"{dup} duplicates"))
    orphan = s.one("SELECT COUNT(*) c FROM box b LEFT JOIN players p ON p.player_id=b.player_id WHERE p.player_id IS NULL")["c"]
    out.append(_chk("orphans", "Every box row references a known player", "pass" if orphan == 0 else "fail", f"{orphan} orphan rows"))
    no_box = s.one("SELECT COUNT(*) c FROM games g WHERE g.status='final' AND NOT EXISTS (SELECT 1 FROM box b WHERE b.game_id=g.game_id)")["c"]
    out.append(_chk("final_without_box", "Every final game has box rows", "pass" if no_box == 0 else "fail", f"{no_box} final games lack box rows"))
    early = s.one("SELECT COUNT(*) c FROM events e JOIN games g ON g.game_id=e.game_id WHERE e.published_at > g.game_date || 'T23:59:59Z' AND g.status='final' AND e.kind='injury' AND e.detail NOT LIKE 'in-game%'")["c"]
    out.append(_chk("pregame_events_timing", "Pregame reports are published before the game day ends", "pass" if early == 0 else "warn", f"{early} reports with late timestamps"))
    pm_bad = 0
    for gid, g in ds.games.items():
        pass
    ratio = float(np.mean(np.abs(tg["pm"]) > 0))
    out.append(_chk("no_ties", "No tied final scores", "pass" if ratio == 1.0 else "fail", f"{(1 - ratio) * 100:.2f}% of team-games tied"))
    labels = {r["data_label"]: r["c"] for r in s.query("SELECT data_label, COUNT(*) c FROM games GROUP BY data_label")}
    out.append(_chk("provenance", "Dataset provenance label", "info" if ds.synthetic else "pass", f"games by label: {labels}. " + ("ALL DATA IS SYNTHETIC FIXTURE - not real." if ds.synthetic else "")))
    miss = [t["abbr"] for tid, t in ds.teams.items() if int((tg["team_id"] == tid).sum()) < 5]
    out.append(_chk("coverage", "Every team has completed games", "pass" if not miss else "warn", f"teams with <5 games: {miss}"))
    tracking_na = [c for c in ("time_of_poss", "deflections", "screen_ast", "shot_loc", "onoff") if True]
    out.append(_chk("unavailable_metrics", "Registered-but-unavailable metrics (never fabricated)", "info", ", ".join(tracking_na) + " need an external provider."))
    return out


def check_leakage(lab, game_id: int = None) -> dict:
    """Corrupt every row at/after a target date, rebuild features, and require identical pregame features."""
    from . import dataset
    ds = lab.ds
    finals = sorted((g["game_date"], gid) for gid, g in ds.games.items() if g["status"] == "final")
    gid = game_id or finals[int(len(finals) * 0.6)][1]
    g = ds.games[gid]
    d0 = g["game_date"]
    as_of = asof_for_game(d0)
    src = lab.store
    mem = Store(":memory:")
    src.conn.backup(mem.conn)
    mem.execute("UPDATE box SET pts = pts + 11, min = min * 0.5, ast = ast + 9, reb = reb + 5, fga = fga + 3 WHERE game_id IN (SELECT game_id FROM games WHERE game_date >= ?)", (d0,))
    mem.execute("UPDATE games SET home_score = home_score + 13 WHERE game_date >= ?", (d0,))
    mem.execute("DELETE FROM events WHERE published_at > ?", (as_of,))
    mem.execute("INSERT INTO events (source, kind, team_id, player_id, game_id, status, detail, published_at, retrieved_at, data_label) "
                "SELECT 'LEAK_PROBE','injury', team_id, player_id, ?, 'out', 'leak probe', ?, ?, 'PROBE' FROM players WHERE team_id=?",
                (gid, "2099-01-01T00:00:00Z", "2099-01-01T00:00:00Z", g["home_team_id"]))
    ds2 = dataset.build(mem)
    fb2 = FeatureBuilder(ds2, mem)
    diffs = []
    for t in (g["home_team_id"], g["away_team_id"]):
        a = {r["player_id"]: r for r in lab.fb.team_features(gid, t, as_of)}
        b = {r["player_id"]: r for r in fb2.team_features(gid, t, as_of)}
        for pid in a:
            for k, v in a[pid].items():
                w = b[pid][k]
                if isinstance(v, (int, float)) and isinstance(w, (int, float)):
                    if not np.isclose(v, w, rtol=1e-9, atol=1e-9):
                        diffs.append((pid, k, v, w))
                elif v != w:
                    diffs.append((pid, k, v, w))
    mem.close()
    return {"game_id": gid, "date": d0, "as_of": as_of, "features_compared": sum(len(v) for v in a.values()), "differences": diffs[:10], "n_differences": len(diffs),
            "status": "pass" if not diffs else "fail",
            "description": "All rows on/after the target date were corrupted and a future-dated event was injected; pregame features must not change."}


def check_immutability(lab) -> List[dict]:
    out = []
    for table, col in (("predictions", "model_id"), ("snapshots", "game_id"), ("scoring_systems", "name"), ("model_registry", "note")):
        row = lab.store.one(f"SELECT rowid r FROM {table} LIMIT 1")
        if not row:
            out.append(_chk(f"immut_{table}", f"{table} is append-only", "info", "table is empty; trigger exists but untested"))
            continue
        try:
            lab.store.conn.execute(f"UPDATE {table} SET {col}={col} WHERE rowid=?", (row["r"],))
            lab.store.conn.rollback()
            out.append(_chk(f"immut_{table}", f"{table} is append-only", "fail", "UPDATE succeeded"))
        except sqlite3.IntegrityError:
            lab.store.conn.rollback()
            out.append(_chk(f"immut_{table}", f"{table} is append-only", "pass", "UPDATE was rejected by trigger"))
        except sqlite3.DatabaseError as e:
            lab.store.conn.rollback()
            out.append(_chk(f"immut_{table}", f"{table} is append-only", "pass" if "append-only" in str(e) or "immutable" in str(e) else "fail", str(e)))
    return out


def check_reproducibility(lab) -> dict:
    ups = sorted((g["game_date"], gid) for gid, g in lab.ds.games.items() if g["status"] == "scheduled")
    if not ups:
        return _chk("repro", "Projection reproducibility", "warn", "no scheduled games")
    gid = ups[0][1]
    t = lab.ds.games[gid]["home_team_id"]
    m = lab.champion_model()
    a = m.predict_team(gid, t, as_of=lab.clock(), n_draws=120)
    b = m.predict_team(gid, t, as_of=lab.clock(), n_draws=120)
    same = bool(np.array_equal(a.draws, b.draws))
    return _chk("repro", "Same inputs + same seed give identical draws", "pass" if same else "fail", f"game {gid}, model {m.version}")


def full_report(lab, deep: bool = True) -> dict:
    lab.ensure_projections()
    report = {
        "generated_for_clock": lab.clock(), "app_version": __version__, "formula_version": FORMULA_VERSION, "dataset_label": lab.ds.label(),
        "dataset_sha256": data_hash(lab.store), "seed": lab.store.get_meta("seed"), "champion": lab.registry.champion(),
        "model_versions": {k: v.version for k, v in lab.models.items()}, "scoring_versions": {s["id"]: s["version"] for s in lab.scoring.list()},
        "data_quality": data_quality(lab), "immutability": check_immutability(lab), "reproducibility": check_reproducibility(lab),
    }
    if deep:
        report["leakage"] = check_leakage(lab)
    statuses = [c["status"] for c in report["data_quality"] + report["immutability"]] + [report["reproducibility"]["status"]] + ([report["leakage"]["status"]] if deep else [])
    report["summary"] = {"pass": statuses.count("pass"), "fail": statuses.count("fail"), "warn": statuses.count("warn"), "info": statuses.count("info")}
    return report
