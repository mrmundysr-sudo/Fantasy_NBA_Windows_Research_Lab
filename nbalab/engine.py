"""Deterministic calculation engine.

Every number shown anywhere in the lab comes from `Engine.run` over actual stored rows.
The engine never calls a model to produce statistics and never executes SQL text.

Structured query (all keys optional unless noted)::

    {
      "entity": "player" | "team" | "coach" | "lineup",
      "metrics": [ {"id": "pts", "transform": "per36"}, {"id": "ts_pct"}, ... ],   # no count cap
      "custom": [ {"name": "Scoring Blend", "formula": "pts_per36 * 0.7 + ast_per36 * 0.3"} ],
      "composite": {"name": "Z Score", "weights": {"pts_per36": 1, "tov_per36": -1}},
      "window":  {"seasons": ["SYN-2"], "date_from": "...", "date_to": "...", "last_n": 10},
      "filters": {"home": true, "opponents": [3], "result": "W", "started": true, "b2b": true,
                  "rest_min": 2, "rest_max": 5, "min_minutes_game": 20, "ot": false,
                  "teammates_out": [12], "teammates_in": [14], "coach_ids": [2]},
      "ids": [..], "team_ids": [..], "position": "PG",
      "min_games": 5, "min_minutes": 100,
      "sort": {"by": "pts", "desc": true}, "limit": 25,
      "uncertainty": true
    }
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import FORMULA_VERSION, registry
from .dataset import Dataset, played_flag
from .registry import Metric
from .safe_eval import FormulaError, evaluate, names_in

ENTITIES = ("player", "team", "coach", "lineup")
MAX_CELLS = 2_000_000
MAX_SECONDS = 20.0
TRANSFORM_SUFFIX = {"_per_game": "per_game", "_per36": "per36", "_per100": "per100"}


class QueryError(ValueError):
    """The structured query is malformed or asks for something that is not valid."""


class MetricError(QueryError):
    """A metric is unknown, unavailable, or the normalization is not mathematically valid."""


def _ord(s: str) -> int:
    return date.fromisoformat(s).toordinal()


def parse_metric_token(token: str) -> Tuple[str, str]:
    """'pts_per36' -> ('pts','per36'); 'ts_pct' -> ('ts_pct','raw')."""
    for suf, tr in TRANSFORM_SUFFIX.items():
        if token.endswith(suf) and token[: -len(suf)] in _known_ids():
            return token[: -len(suf)], tr
    return token, "raw"


def _known_ids():
    return {m.id for m in registry.all_metrics()}


def canonical(q: dict) -> str:
    return json.dumps(q, sort_keys=True, separators=(",", ":"), default=str)


def query_hash(q: dict) -> str:
    return hashlib.sha256(canonical(q).encode()).hexdigest()[:16]


class Engine:
    def __init__(self, ds: Dataset, projections=None):
        self.ds = ds
        self.projections = projections  # callable(player_ids) -> {col: array} or None
        pg, tg = ds.pg, ds.tg
        pg["poss_on"] = np.where(pg["tm_min"] > 0, pg["tm_poss"] * pg["min"] / (pg["tm_min"] / 5.0), 0.0)
        tg["poss_on"] = tg["tm_poss"].copy()
        self._pg_sorted = None

    # -------------------------------------------------------------------------------------------
    # metric helpers
    # -------------------------------------------------------------------------------------------
    def check_metric(self, mid: str, transform: str, entity: str) -> Metric:
        try:
            m = registry.get(mid)
        except KeyError:
            raise MetricError(f"Unknown metric '{mid}'. Use the metric registry search to find supported metrics.") from None
        if m.status == "unavailable":
            raise MetricError(f"Metric '{m.name}' is unavailable: {m.unavailable_reason}")
        if entity not in m.scope:
            raise MetricError(f"Metric '{m.name}' is not defined for {entity} entities (valid for: {', '.join(m.scope)}).")
        if transform not in registry.ALL_TRANSFORMS:
            raise MetricError(f"Unknown normalization '{transform}'.")
        if transform != "raw":
            if transform not in m.transforms:
                why = {"pct": "a percentage", "ratio": "a ratio", "rating": "already a per-100-possession rating",
                       "rate": "already a rate", "minutes": "minutes", "score": "a score"}.get(m.unit, m.unit)
                raise MetricError(f"Normalization '{transform}' is not valid for '{m.name}' ({why}); valid: {', '.join(m.transforms)}.")
            if entity != "player" and transform == "per36":
                raise MetricError("Per-36 normalization is only defined for individual players.")
            if m.kind == "projected":
                raise MetricError("Projected metrics cannot be normalized.")
        return m

    def _arrays(self, entity: str):
        return self.ds.pg if entity == "player" else self.ds.tg

    def _entity_key(self, entity: str) -> str:
        return {"player": "player_id", "team": "team_id", "coach": "coach_id", "lineup": "lineup_id"}[entity]

    def entity_name(self, entity: str, eid: int) -> str:
        ds = self.ds
        if entity == "player":
            return ds.players[eid]["name"]
        if entity == "team":
            return ds.teams[eid]["name"]
        if entity == "coach":
            return ds.coaches[eid]["name"]
        return ds.lineup_names.get(eid, f"lineup {eid}")

    # -------------------------------------------------------------------------------------------
    # normalise / validate a query
    # -------------------------------------------------------------------------------------------
    def normalize(self, q: dict) -> dict:
        if not isinstance(q, dict):
            raise QueryError("query must be an object")
        out = {
            "entity": q.get("entity", "player"),
            "metrics": [],
            "custom": list(q.get("custom") or []),
            "composite": q.get("composite"),
            "window": dict(q.get("window") or {}),
            "filters": dict(q.get("filters") or {}),
            "ids": [int(i) for i in (q.get("ids") or [])],
            "team_ids": [int(i) for i in (q.get("team_ids") or [])],
            "position": q.get("position"),
            "min_games": int(q.get("min_games", 1)),
            "min_minutes": float(q.get("min_minutes", 0)),
            "sort": dict(q.get("sort") or {}),
            "limit": int(q.get("limit", 25)),
            "uncertainty": bool(q.get("uncertainty", True)),
            "compare": bool(q.get("compare", False)),
            "correlate": q.get("correlate"),
        }
        if out["entity"] not in ENTITIES:
            raise QueryError(f"entity must be one of {ENTITIES}")
        for m in q.get("metrics") or []:
            if isinstance(m, str):
                mid, tr = parse_metric_token(m)
                m = {"id": mid, "transform": tr}
            mid = m.get("id")
            tr = m.get("transform", "raw")
            self.check_metric(mid, tr, out["entity"])
            out["metrics"].append({"id": mid, "transform": tr})
        if out["limit"] < 1 or out["limit"] > 5000:
            raise QueryError("limit must be between 1 and 5000")
        w = out["window"]
        for k in ("date_from", "date_to"):
            if w.get(k):
                try:
                    _ord(w[k])
                except ValueError:
                    raise QueryError(f"window.{k} must be an ISO date (YYYY-MM-DD)") from None
        for s in w.get("seasons") or []:
            if s not in self.ds.seasons:
                raise QueryError(f"Unknown season '{s}'. Available: {', '.join(self.ds.seasons)}")
        if w.get("last_n") is not None and int(w["last_n"]) < 1:
            raise QueryError("window.last_n must be >= 1")
        return out

    # -------------------------------------------------------------------------------------------
    # row selection
    # -------------------------------------------------------------------------------------------
    def row_mask(self, q: dict) -> np.ndarray:
        ds = self.ds
        entity = q["entity"]
        A = self._arrays(entity)
        n = len(A["game_id"])
        mask = np.ones(n, dtype=bool)
        if entity == "player":
            mask &= A["min"] > 0
        w, f = q["window"], q["filters"]
        if w.get("seasons"):
            idx = [ds.seasons.index(s) for s in w["seasons"]]
            mask &= np.isin(A["season"], idx)
        if w.get("date_from"):
            mask &= A["date"] >= _ord(w["date_from"])
        if w.get("date_to"):
            mask &= A["date"] <= _ord(w["date_to"])
        if f.get("home") is not None:
            mask &= A["home"] == (1 if f["home"] else 0)
        if f.get("opponents"):
            mask &= np.isin(A["opp_team_id"], [int(x) for x in f["opponents"]])
        if f.get("result") in ("W", "L"):
            wins = A["win"] if entity == "player" else A["wins"]
            mask &= (wins > 0) if f["result"] == "W" else (wins == 0)
        if f.get("b2b") is not None:
            mask &= A["b2b"] == (1 if f["b2b"] else 0)
        if f.get("rest_min") is not None:
            mask &= A["rest_days"] >= int(f["rest_min"])
        if f.get("rest_max") is not None:
            mask &= A["rest_days"] <= int(f["rest_max"])
        if f.get("ot") is not None:
            mask &= (A["ot"] > 0) == bool(f["ot"])
        if f.get("coach_ids"):
            mask &= np.isin(A["coach_id"], [int(x) for x in f["coach_ids"]])
        if entity == "player":
            if f.get("started") is not None:
                mask &= A["started"] == (1 if f["started"] else 0)
            if f.get("min_minutes_game") is not None:
                mask &= A["min"] >= float(f["min_minutes_game"])
        gt = ds.pg_gt if entity == "player" else None
        for pid in f.get("teammates_out") or []:
            pid = int(pid)
            if pid not in ds.players:
                raise QueryError(f"Unknown teammate id {pid}")
            flag = played_flag(ds, pid)
            tm = ds.players[pid]["team_id"]
            same_team = (ds.tg["team_id"] == tm)
            cond = (~flag) & same_team
            mask &= (cond[gt] if gt is not None else cond)
        for pid in f.get("teammates_in") or []:
            pid = int(pid)
            if pid not in ds.players:
                raise QueryError(f"Unknown teammate id {pid}")
            flag = played_flag(ds, pid)
            mask &= (flag[gt] if gt is not None else flag)
        key = self._entity_key(entity)
        if q["ids"]:
            mask &= np.isin(A[key], q["ids"])
        if q["team_ids"]:
            mask &= np.isin(A["team_id"], q["team_ids"])
        if entity == "player" and q.get("position"):
            pos_ids = [p for p, r in ds.players.items() if r["position"] == q["position"]]
            mask &= np.isin(A["player_id"], pos_ids)
        idx = np.nonzero(mask)[0]
        if w.get("last_n"):
            idx = self._last_n(A, key, idx, int(w["last_n"]))
        return idx

    @staticmethod
    def _last_n(A, key, idx, n):
        if len(idx) == 0:
            return idx
        ent, dt, gid = A[key][idx], A["date"][idx], A["game_id"][idx]
        order = np.lexsort((-gid, -dt, ent))
        e_sorted = ent[order]
        starts = np.r_[0, np.nonzero(np.diff(e_sorted))[0] + 1]
        counts = np.diff(np.r_[starts, len(e_sorted)])
        rank = np.arange(len(e_sorted)) - np.repeat(starts, counts)
        keep = order[rank < n]
        return np.sort(idx[keep])

    # -------------------------------------------------------------------------------------------
    # evaluation of metric values from window sums
    # -------------------------------------------------------------------------------------------
    @staticmethod
    def _needed_cols(specs: Sequence[Tuple[Metric, str]]) -> set:
        cols = {"min", "gp", "poss_on"}
        for m, tr in specs:
            if m.expr:
                cols |= set(m.dependencies)
            else:
                cols.add(m.id)
        return cols

    def metric_from_sums(self, m: Metric, transform: str, sums: Dict[str, np.ndarray], entity: str):
        if m.kind == "projected":
            raise MetricError("internal: projected metrics are not computed from sums")
        base = evaluate(m.expr, sums) if m.expr else sums[m.id]
        base = np.asarray(base, dtype=float)
        if transform == "raw":
            return base
        if transform == "per_game":
            return np.where(sums["gp"] > 0, base / np.where(sums["gp"] > 0, sums["gp"], 1), np.nan)
        if transform == "per36":
            return np.where(sums["min"] > 0, base * 36.0 / np.where(sums["min"] > 0, sums["min"], 1), np.nan)
        if transform == "per100":
            return np.where(sums["poss_on"] > 0, base * 100.0 / np.where(sums["poss_on"] > 0, sums["poss_on"], 1), np.nan)
        raise MetricError(f"unknown normalization {transform}")

    def _group(self, A, key, idx):
        ent = A[key][idx]
        uniq, inv = np.unique(ent, return_inverse=True)
        return uniq, inv

    def _sums(self, A, idx, inv, G, cols):
        out = {}
        for c in cols:
            if c not in A:
                raise MetricError(f"data column '{c}' is not present in this dataset")
            out[c] = np.bincount(inv, weights=A[c][idx], minlength=G)
        return out

    # -------------------------------------------------------------------------------------------
    # main entry
    # -------------------------------------------------------------------------------------------
    def run(self, q_in: dict) -> dict:
        t0 = time.time()
        q = self.normalize(q_in)
        ds = self.ds
        entity = q["entity"]
        A = self._arrays(entity)
        key = self._entity_key(entity)
        warnings: List[str] = []
        if not q["metrics"] and not q["custom"] and not q["composite"] and not q["correlate"]:
            q["metrics"] = [{"id": "gp", "transform": "raw"}, {"id": "min", "transform": "per_game"}]
            warnings.append("No metrics were requested; defaulted to games played and minutes per game.")

        specs: List[Tuple[Metric, str, str]] = []
        for m in q["metrics"]:
            met = registry.get(m["id"])
            specs.append((met, m["transform"], _label(met, m["transform"])))
        proj_specs = [s for s in specs if s[0].kind == "projected"]
        calc_specs = [s for s in specs if s[0].kind != "projected"]

        # custom formulas / composite may reference more metrics: collect dependencies
        custom_defs = []
        extra_specs: Dict[str, Tuple[Metric, str]] = {}
        for c in q["custom"]:
            if not c.get("formula") or not c.get("name"):
                raise QueryError("custom formulas need 'name' and 'formula'")
            try:
                deps = names_in(c["formula"])
            except FormulaError as e:
                raise QueryError(f"Custom formula '{c['name']}' is invalid: {e}") from None
            for d in deps:
                mid, tr = parse_metric_token(d)
                met = self.check_metric(mid, tr, entity)
                if met.kind == "projected":
                    raise QueryError("custom formulas cannot reference projected metrics")
                extra_specs[d] = (met, tr)
            custom_defs.append(c)
        comp = q.get("composite")
        if comp:
            if not comp.get("weights"):
                raise QueryError("composite needs 'weights'")
            for d in comp["weights"]:
                mid, tr = parse_metric_token(d)
                met = self.check_metric(mid, tr, entity)
                extra_specs[d] = (met, tr)
        corr = q.get("correlate")
        corr_specs: Dict[str, Tuple[Metric, str]] = {}
        if corr:
            if len(corr.get("metrics", [])) < 2:
                raise QueryError("correlate needs at least two metrics")
            for d in corr["metrics"]:
                mid, tr = parse_metric_token(d)
                corr_specs[d] = (self.check_metric(mid, tr, entity), tr)

        n_cells_est = len(ds.players if entity == "player" else ds.teams) * (len(specs) + len(extra_specs) + len(custom_defs))
        if n_cells_est > MAX_CELLS:
            raise QueryError(f"Query too large ({n_cells_est:,} cells > {MAX_CELLS:,}); narrow entities or metrics.")

        idx = self.row_mask(q)
        n_rows = int(len(idx))
        if n_rows == 0:
            return self._empty(q, warnings + ["No rows matched the window and filters."], t0)
        uniq, inv = self._group(A, key, idx)
        G = len(uniq)
        all_specs = [(m, tr) for m, tr, _ in calc_specs] + list(extra_specs.values()) + list(corr_specs.values())
        cols = self._needed_cols(all_specs)
        sums = self._sums(A, idx, inv, G, cols)

        keep = np.ones(G, dtype=bool)
        gp_total = sums["gp"]
        keep &= gp_total >= q["min_games"]
        keep &= sums["min"] >= q["min_minutes"]
        results: Dict[str, np.ndarray] = {}
        columns = []
        for m, tr, label in calc_specs:
            if time.time() - t0 > MAX_SECONDS:
                raise QueryError("Compute time budget exceeded; reduce metrics or entities.")
            results[label] = self.metric_from_sums(m, tr, sums, entity)
            columns.append(self._col_info(m, tr, label))
        # helper values for custom / composite / correlation (not displayed unless requested)
        helper: Dict[str, np.ndarray] = {}
        for token, (m, tr) in {**extra_specs, **corr_specs}.items():
            helper[token] = self.metric_from_sums(m, tr, sums, entity)
        for c in custom_defs:
            try:
                results[c["name"]] = np.asarray(evaluate(c["formula"], helper), dtype=float) * np.ones(G)
            except FormulaError as e:
                raise QueryError(f"Custom formula '{c['name']}' failed: {e}") from None
            columns.append({"label": c["name"], "id": "custom", "transform": "raw", "kind": "derived", "unit": "custom",
                            "formula": c["formula"], "formula_version": FORMULA_VERSION,
                            "dependencies": sorted(names_in(c["formula"])), "source": "user formula"})
        if comp:
            wts = comp["weights"]
            z_total = np.zeros(G)
            pop = keep.copy()
            for token, wgt in wts.items():
                v = helper[token]
                mu, sd = np.nanmean(v[pop]), np.nanstd(v[pop])
                z = (v - mu) / sd if sd and not np.isnan(sd) else np.zeros(G)
                z_total += float(wgt) * np.nan_to_num(z, nan=0.0)
            results[comp.get("name", "Composite")] = z_total
            columns.append({"label": comp.get("name", "Composite"), "id": "composite", "transform": "raw", "kind": "derived", "unit": "z-score",
                            "formula": "sum(weight * zscore(metric))", "formula_version": FORMULA_VERSION,
                            "dependencies": sorted(wts), "source": "user weights", "weights": wts})
            warnings += self._collinearity_warning(helper, list(wts), pop)

        # projected metrics come from the model store (player entities only)
        if proj_specs:
            if entity != "player" or self.projections is None:
                raise MetricError("Projected metrics require player entities and a loaded projection store.")
            pj = self.projections(list(uniq))
            for m, tr, label in proj_specs:
                results[label] = np.array([pj.get(int(e), {}).get(m.id, np.nan) for e in uniq], dtype=float)
                columns.append(self._col_info(m, tr, label))
                warnings.append(f"'{m.name}' is a model projection ({'SYNTHETIC demonstration' if ds.synthetic else 'model output'}), not an observed value.")

        sel = np.nonzero(keep)[0]
        if len(sel) == 0:
            return self._empty(q, warnings + ["Entities matched but none met min_games / min_minutes thresholds."], t0, rows=n_rows)
        # sorting
        sort = q["sort"]
        sort_label = None
        if sort.get("by"):
            sort_label = self._resolve_label(sort["by"], results, columns)
            if sort_label is None:
                raise QueryError(f"Cannot sort by '{sort['by']}': not among the result columns.")
        elif results:
            sort_label = columns[0]["label"] if columns else None
        order = sel
        if sort_label is not None:
            v = results[sort_label][sel]
            desc = bool(sort.get("desc", True))
            fill = -np.inf if desc else np.inf
            vv = np.where(np.isnan(v), fill, v)
            order = sel[np.argsort(-vv if desc else vv, kind="stable")]
        total_entities = int(len(order))
        order = order[: q["limit"]]
        # uncertainty
        ci = {}
        if q["uncertainty"] and calc_specs and len(order) <= 120:
            ci = self._bootstrap(q, A, key, idx, uniq, inv, order, calc_specs, entity)
        elif q["uncertainty"] and calc_specs:
            warnings.append("Uncertainty intervals omitted: more than 120 result rows (compute safeguard).")
        rows = []
        for rank, gi in enumerate(order, 1):
            eid = int(uniq[gi])
            r = {"rank": rank, "id": eid, "name": self.entity_name(entity, eid), "games": int(sums["gp"][gi]) if "gp" in sums else None,
                 "minutes": round(float(sums["min"][gi]), 1)}
            r.update(self._entity_extra(entity, eid))
            vals = {}
            for label in results:
                x = float(results[label][gi])
                vals[label] = None if (math.isnan(x) or math.isinf(x)) else round(x, 6)
            r["values"] = vals
            if ci:
                r["ci90"] = {k: ci[k][rank - 1] for k in ci}
            rows.append(r)
        out = {
            "query": q, "query_hash": query_hash(q), "columns": columns, "rows": rows,
            "meta": self._meta(q, n_rows, total_entities, int(len(idx)), warnings, t0),
        }
        if q["compare"] and len(rows) >= 2:
            out["compare"] = self._compare(rows, results, uniq, keep, columns)
        if corr:
            out["correlation"] = self._correlation(q, corr, corr_specs, helper, keep, entity, A, idx, inv, G)
        return out

    # -------------------------------------------------------------------------------------------
    def _resolve_label(self, by: str, results, columns):
        if by in results:
            return by
        for c in columns:
            if c["id"] == by or c["label"].lower() == by.lower():
                return c["label"]
        mid, tr = parse_metric_token(by)
        for c in columns:
            if c["id"] == mid and c["transform"] == tr:
                return c["label"]
        return None

    def _entity_extra(self, entity, eid):
        ds = self.ds
        if entity == "player":
            p = ds.players[eid]
            t = ds.teams.get(p["team_id"], {})
            return {"team": t.get("abbr"), "team_id": p["team_id"], "position": p["position"]}
        if entity == "team":
            return {"team": ds.teams[eid]["abbr"], "team_id": eid}
        return {}

    def _col_info(self, m: Metric, tr: str, label: str) -> dict:
        return {"label": label, "id": m.id, "name": m.name, "transform": tr, "kind": m.kind, "unit": m.unit,
                "formula": m.expr or f"sum({m.id})", "formula_version": m.formula_version, "dependencies": list(m.dependencies) or [m.id],
                "source": m.source, "better": m.better, "description": m.description}

    def _meta(self, q, n_rows, n_entities, n_rows_used, warnings, t0):
        ds = self.ds
        last = max((g["game_date"] for g in ds.games.values() if g["status"] == "final"), default=None)
        return {"rows_matched": n_rows, "entities_matched": n_entities, "dataset_label": ds.label(), "synthetic": ds.synthetic,
                "seasons_available": ds.seasons, "last_game_date": last, "formula_version": FORMULA_VERSION,
                "compute_ms": round((time.time() - t0) * 1000, 1), "warnings": warnings,
                "possession_basis": "Possessions estimated per game as the mean of both teams' (FGA + 0.44*FTA - OREB + TOV)."}

    def _empty(self, q, warnings, t0, rows=0):
        return {"query": q, "query_hash": query_hash(q), "columns": [], "rows": [], "meta": self._meta(q, rows, 0, rows, warnings, t0)}

    def _collinearity_warning(self, helper, tokens, pop):
        out = []
        for i in range(len(tokens)):
            for j in range(i + 1, len(tokens)):
                a, b = helper[tokens[i]][pop], helper[tokens[j]][pop]
                ok = ~(np.isnan(a) | np.isnan(b))
                if ok.sum() > 8 and np.std(a[ok]) > 0 and np.std(b[ok]) > 0:
                    r = float(np.corrcoef(a[ok], b[ok])[0, 1])
                    if abs(r) >= 0.9:
                        out.append(f"'{tokens[i]}' and '{tokens[j]}' are highly correlated (r={r:.2f}); weighting both double-counts the same signal.")
        return out

    # -------------------------------------------------------------------------------------------
    def _bootstrap(self, q, A, key, idx, uniq, inv, order, calc_specs, entity, B=300):
        seed = int(query_hash(q)[:8], 16)
        rng = np.random.default_rng(seed)
        cols = sorted(self._needed_cols([(m, tr) for m, tr, _ in calc_specs]))
        by_ent = np.argsort(inv, kind="stable")
        bounds = np.searchsorted(inv[by_ent], np.arange(len(uniq) + 1))
        out = {label: [] for _, _, label in calc_specs}
        for gi in order:
            rows = idx[by_ent[bounds[gi]: bounds[gi + 1]]]
            n = len(rows)
            if n < 3:
                for label in out:
                    out[label].append(None)
                continue
            draw = rng.integers(0, n, size=(B, n))
            sums = {c: A[c][rows][draw].sum(axis=1) for c in cols}
            for m, tr, label in calc_specs:
                if m.kind == "projected":
                    out[label].append(None)
                    continue
                v = self.metric_from_sums(m, tr, sums, entity)
                v = v[~np.isnan(v)]
                out[label].append([round(float(np.percentile(v, 5)), 6), round(float(np.percentile(v, 95)), 6)] if len(v) > 10 else None)
        return out

    def _compare(self, rows, results, uniq, keep, columns):
        pop = np.nonzero(keep)[0]
        base = rows[0]
        out = {"baseline": base["name"], "metrics": []}
        for c in columns:
            lab = c["label"]
            v_all = results[lab][pop]
            v_all = v_all[~np.isnan(v_all)]
            entry = {"label": lab, "entities": []}
            for r in rows:
                x = r["values"].get(lab)
                pr = None
                if x is not None and len(v_all) > 1:
                    pr = round(float((v_all < x).mean() + 0.5 * (v_all == x).mean()) * 100, 1)
                entry["entities"].append({"id": r["id"], "name": r["name"], "value": x,
                                          "delta_vs_baseline": None if (x is None or base["values"].get(lab) is None) else round(x - base["values"][lab], 6),
                                          "percentile": pr})
            out["metrics"].append(entry)
        return out

    # -------------------------------------------------------------------------------------------
    def _correlation(self, q, corr, corr_specs, helper, keep, entity, A, idx, inv, G):
        tokens = list(corr["metrics"])
        level = corr.get("level", "entity")
        method = corr.get("method", "pearson")
        if level == "entity":
            data = np.stack([helper[t][keep] for t in tokens], axis=1)
            unit = "entities"
        elif level == "game":
            rows_sums = {c: A[c][idx] for c in self._needed_cols(list(corr_specs.values()))}
            mats = []
            for t in tokens:
                m, tr = corr_specs[t]
                mats.append(self.metric_from_sums(m, tr, rows_sums, entity))
            data = np.stack(mats, axis=1)
            unit = "player-games" if entity == "player" else "team-games"
            keep_rows = keep[inv]
            data = data[keep_rows]
        else:
            raise QueryError("correlate.level must be 'entity' or 'game'")
        k = len(tokens)
        r = [[None] * k for _ in range(k)]
        p = [[None] * k for _ in range(k)]
        nn = [[0] * k for _ in range(k)]
        for i in range(k):
            for j in range(k):
                a, b = data[:, i], data[:, j]
                ok = ~(np.isnan(a) | np.isnan(b))
                nn[i][j] = int(ok.sum())
                if ok.sum() < 4 or np.std(a[ok]) == 0 or np.std(b[ok]) == 0:
                    continue
                x, y = a[ok], b[ok]
                if method == "spearman":
                    x, y = _rank(x), _rank(y)
                elif method != "pearson":
                    raise QueryError("correlate.method must be pearson or spearman")
                rv = float(np.corrcoef(x, y)[0, 1])
                r[i][j] = round(rv, 4)
                p[i][j] = _corr_p(rv, int(ok.sum())) if i != j else 0.0
        return {"metrics": tokens, "method": method, "level": level, "unit": unit, "r": r, "p_value": p, "n": nn,
                "note": "Correlation is descriptive. Entity-level correlations with few entities are unstable; p-values assume independent rows."}

    # -------------------------------------------------------------------------------------------
    # supporting game rows
    # -------------------------------------------------------------------------------------------
    def support(self, q_in: dict, entity_id: int, limit: int = 200) -> dict:
        """Games that contributed to an entity's aggregate under the same window and filters."""
        q = self.normalize(q_in)
        entity = q["entity"]
        A = self._arrays(entity)
        key = self._entity_key(entity)
        q2 = dict(q)
        q2["ids"] = [int(entity_id)]
        idx = self.row_mask(q2)
        ds = self.ds
        cols = ["min", "pts", "reb", "ast"] if entity == "player" else ["pts", "opp_pts"]
        used = {m["id"] for m in q["metrics"]}
        for u in used:
            met = registry.get(u)
            cols += list(met.dependencies) if met.expr else ([u] if u in A else [])
        seen, cols2 = set(), []
        for c in cols:
            if c in A and c not in seen:
                seen.add(c)
                cols2.append(c)
        order = np.lexsort((-A["game_id"][idx], -A["date"][idx]))
        idx = idx[order]
        rows = []
        for i in idx[:limit]:
            gid = int(A["game_id"][i])
            g = ds.games[gid]
            opp = int(A["opp_team_id"][i])
            wins = (A["win"][i] if entity == "player" else A["wins"][i]) > 0
            rows.append({"game_id": gid, "date": g["game_date"], "season": g["season"], "opponent": ds.teams[opp]["abbr"],
                         "home": bool(A["home"][i]), "result": "W" if wins else "L",
                         "score": f"{g['home_score']}-{g['away_score']}" + (f" ({g['ot']}OT)" if g["ot"] else ""),
                         "stats": {c: _num(A[c][i]) for c in cols2}})
        totals = {c: _num(A[c][idx].sum()) for c in cols2}
        return {"entity": entity, "id": int(entity_id), "name": self.entity_name(entity, int(entity_id)), "games_total": int(len(idx)),
                "shown": len(rows), "rows": rows, "column_totals": totals, "dataset_label": ds.label()}


def _num(x):
    x = float(x)
    return int(x) if x == int(x) else round(x, 3)


def _label(m: Metric, tr: str) -> str:
    return m.name if tr == "raw" else f"{m.name} ({ {'per_game': 'per game', 'per36': 'per 36', 'per100': 'per 100 poss'}[tr] })"


def _rank(x):
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x))
    # average ties
    _, inv, counts = np.unique(x, return_inverse=True, return_counts=True)
    sums = np.bincount(inv, weights=ranks)
    return (sums / counts)[inv]


def _betacf(a, b, x):
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        de = d * c
        h *= de
        if abs(de - 1.0) < 3e-12:
            break
    return h


def _betainc(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1 - bt * _betacf(b, a, 1 - x) / b


def _corr_p(r: float, n: int) -> Optional[float]:
    """Two-sided p-value for H0: rho = 0 using the t distribution (exact via incomplete beta)."""
    if n < 4 or abs(r) >= 1:
        return 0.0 if abs(r) >= 1 else None
    df = n - 2
    t2 = r * r * df / (1 - r * r)
    return float(_betainc(df / 2.0, 0.5, df / (df + t2)))
