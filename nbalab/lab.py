"""Application context: wires store, dataset, engine, NL query, models, events and projector together."""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import date, datetime
from typing import Dict, List, Optional

from . import __version__, dataset, synthetic
from .engine import Engine
from .evaluation import LabCandidate, MeanCandidate, ModelRegistry, run_tournament
from .events import EventManager
from .features import FeatureBuilder
from .models import FeatureCache, LabModel
from .nlq import NLQ
from .projector import Projector
from .providers.adapters import CsvEventProvider, DemoScriptProvider, HttpJsonProvider
from .sources import SourceRouter
from .sources.events_bridge import SourceEventAdapter
from .scoring import ScoringRegistry
from .store import Store, default_data_dir, utcnow


class Lab:
    def __init__(self, store: Store, n_draws: int = 300):
        self.store = store
        self.n_draws = n_draws
        self.lock = threading.RLock()
        self.jobs: Dict[str, dict] = {}
        self._load()

    # ---------------------------------------------------------------------------------------------
    @classmethod
    def open(cls, path: Optional[str] = None, seed: int = 7, n_draws: int = 300, **gen_kwargs) -> "Lab":
        """Open (or create) the lab database. A new database is filled with the SYNTHETIC fixture."""
        if path is None:
            path = os.path.join(default_data_dir(), "lab.sqlite")
        store = Store(path)
        if not store.has_data():
            synthetic.generate(store, seed=seed, **gen_kwargs)
        return cls(store, n_draws=n_draws)

    def _load(self) -> None:
        s = self.store
        self.ds = dataset.build(s)
        self.engine = Engine(self.ds)
        self.nlq = NLQ(self.engine)
        self.fb = FeatureBuilder(self.ds, s)
        self.cache = FeatureCache(self.fb)
        self.scoring = ScoringRegistry(s)
        self.registry = ModelRegistry(s)
        self.models: Dict[str, LabModel] = {}
        self.events = EventManager(s, self.ds, self.fb, self.clock, on_new_events=self._on_events)
        self.projector = Projector(s, self.ds, self.fb, self.cache, self.champion_model, self.events, self.clock)
        self.demo = DemoScriptProvider(self.ds, s, self.fb)
        if self.ds.synthetic:                       # the scripted demo feed only ever exists beside synthetic data
            self.events.register(self.demo)
        self.csv_provider = CsvEventProvider(None)
        self.events.register(self.csv_provider)
        self.sources = SourceRouter(s)
        for src, cap in (("NBA_LIVE", "injuries"), ("NBA_LIVE", "starting_lineups"), ("SPORTSDATAIO", "injuries"), ("SPORTSDATAIO", "starting_lineups"),
                         ("NBA_INJURY_REPORT", "injuries"), ("BALLDONTLIE", "injuries"), ("BALLDONTLIE", "starting_lineups")):
            self.events.register(SourceEventAdapter(self.sources, src, cap))
        self.events.register(HttpJsonProvider("SPORTRADAR"))
        self.engine.projections = self.projector.projections_for_engine(self.scoring.get("classic_points")["rules"])
        self.trained_through: Optional[str] = None

    # ---------------------------------------------------------------------------------------------
    def clock(self) -> str:
        """Lab clock. For the synthetic fixture this is the fixture's own 'now'; with real data it is wall-clock UTC."""
        if self.store.get_meta("synthetic", False):
            base = self.store.get_meta("demo_as_of")
            off = self.store.get_meta("demo_clock_offset_min", 0)
            from datetime import timedelta
            return (datetime.strptime(base[:19], "%Y-%m-%dT%H:%M:%S") + timedelta(minutes=off)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return utcnow()

    def last_final_ord(self) -> int:
        return max(date.fromisoformat(g["game_date"]).toordinal() for g in self.ds.games.values() if g["status"] == "final")

    # ---------------------------------------------------------------------------------------------
    def train(self, which: Optional[List[str]] = None) -> dict:
        """Fit the lab models on all completed games (strictly before the first scheduled game)."""
        cutoff = self.last_final_ord() + 1
        out = {}
        defs = {"lab_v1": 1.0, "lab_blend": 0.5}
        with self.lock:
            for name, blend in defs.items():
                if which and name not in which:
                    continue
                m = LabCandidate(self.ds, self.fb, self.cache, name=name, blend=blend, n_draws=self.n_draws)
                out[name] = m.fit(cutoff)
                self.models[name] = m
            self.trained_through = date.fromordinal(cutoff - 1).isoformat()
        return out

    def champion_model(self) -> LabModel:
        if not self.models:
            self.train()
        name = self.registry.champion()
        return self.models.get(name) or self.models["lab_v1"]

    def _on_events(self, inserted: List[dict], as_of: str) -> List[dict]:
        return self.projector.recompute_for_events(inserted, as_of)

    # ---------------------------------------------------------------------------------------------
    def ensure_projections(self, days: int = 4) -> dict:
        if not self.models:
            self.train()
        if not self.store.one("SELECT 1 FROM predictions LIMIT 1"):
            return self.projector.project_upcoming(self.clock(), days)
        return {"games": 0, "predictions_written": 0, "note": "projections already stored"}

    def deliver_demo_report(self) -> dict:
        """Deliver the next scripted SYNTHETIC injury/lineup report and recompute affected projections."""
        if not self.ds.synthetic:
            raise ValueError("the scripted demo feed exists only with the SYNTHETIC fixture")
        self.ensure_projections()
        res = self.events.poll("DEMO_SCRIPT", recompute=True)
        return res

    def reset_demo(self) -> None:
        if not self.ds.synthetic:
            raise ValueError("the scripted demo feed exists only with the SYNTHETIC fixture")
        self.demo.reset()

    # ---------------------------------------------------------------------------------------------
    def start_tournament(self, n_folds: int = 3, n_draws: int = 80, max_games_per_fold: Optional[int] = None) -> str:
        job_id = f"tournament-{int(time.time())}"
        job = {"id": job_id, "state": "running", "progress": [], "started": utcnow(), "result": None, "error": None}
        self.jobs[job_id] = job

        def work():
            try:
                cands = [MeanCandidate(self.ds, self.fb, self.cache, "baseline_season_mean", "season", "cum"),
                         MeanCandidate(self.ds, self.fb, self.cache, "baseline_last5", "last5", "dec"),
                         MeanCandidate(self.ds, self.fb, self.cache, "ewma_shrunk", "ewm", "shrunk", True),
                         LabCandidate(self.ds, self.fb, self.cache, name="lab_v1", blend=1.0),
                         LabCandidate(self.ds, self.fb, self.cache, name="lab_blend", blend=0.5)]
                champ = self.registry.champion()
                res = run_tournament(self.ds, self.fb, self.cache, cands, champion_name=champ if champ in {c.name for c in cands} else "lab_v1",
                                     n_folds=n_folds, n_draws=n_draws, progress=lambda m: job["progress"].append(m), max_games_per_fold=max_games_per_fold)
                res["finished"] = utcnow()
                job["result"] = res
                self.store.set_meta("last_tournament", res)
                for name, m in res["models"].items():
                    self.registry.record(name, m["version"], "evaluate", {"fpts_mae": m["metrics"]["fpts"]["mae"], "min_mae": m["metrics"]["min"]["mae"],
                                                                         "calibration": m["metrics"].get("calibration", {}).get("fpts_cover_80")}, f"{res['n_rows']} held-out rows, {n_folds} folds")
                promoted = None
                for name, comp in res["comparisons"].items():
                    if comp["promote"]:
                        self.registry.record(name, res["models"][name]["version"], "promote", {"fpts_improvement": comp["fpts"], "min_improvement": comp["min"]},
                                             "measured improvement vs champion with no calibration regression")
                        promoted = name
                    else:
                        self.registry.record(name, res["models"][name]["version"], "reject", {"reasons": comp["reasons"]}, f"not promoted over {res['champion']}")
                job["promoted"] = promoted
                job["state"] = "done"
            except Exception as e:  # pragma: no cover - surfaced to the UI
                job["state"] = "error"
                job["error"] = f"{type(e).__name__}: {e}"
                job["trace"] = traceback.format_exc()

        threading.Thread(target=work, daemon=True).start()
        return job_id

    def status(self) -> dict:
        ds = self.ds
        finals = [g for g in ds.games.values() if g["status"] == "final"]
        return {
            "app": "NBA Windows Research Lab", "version": __version__, "dataset_label": ds.label(), "synthetic": ds.synthetic,
            "dataset_note": ds.meta.get("dataset_note", ""), "players": len(ds.players), "teams": len(ds.teams), "games_final": len(finals),
            "games_scheduled": len(ds.games) - len(finals), "player_game_rows": int(len(ds.pg["game_id"])), "seasons": ds.seasons,
            "last_game_date": max(g["game_date"] for g in finals), "lab_clock": self.clock(), "metrics_registered": len(__import__("nbalab.registry", fromlist=["x"]).all_metrics()),
            "champion": self.registry.champion(), "trained_through": self.trained_through, "projections_stored": self.store.one("SELECT COUNT(*) c FROM predictions")["c"],
            "events_stored": self.store.one("SELECT COUNT(*) c FROM events")["c"], "import_info": self.store.get_meta("import_info"),
            "db_path": getattr(self.store, "path", None),
        }
