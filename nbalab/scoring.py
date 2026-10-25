"""Fantasy scoring systems. Rule versions are immutable: changing a rule creates a new version."""
from __future__ import annotations

import json
from typing import Dict, List, Optional

import numpy as np

from .store import Store, utcnow

# stat keys usable in rules; 'dd' and 'td' are bonus flags evaluated from the five main categories
STAT_KEYS = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf", "dd", "td"]

PRESETS = {
    "classic_points": {"name": "Classic Points (generic)", "rules": {"pts": 1.0, "reb": 1.2, "ast": 1.5, "stl": 3.0, "blk": 3.0, "tov": -1.0}},
    "dfs_style": {"name": "DFS-style (generic bonuses)", "rules": {"pts": 1.0, "fg3m": 0.5, "reb": 1.25, "ast": 1.5, "stl": 2.0, "blk": 2.0, "tov": -0.5, "dd": 1.5, "td": 3.0}},
    "efficiency": {"name": "Efficiency (PTS+REB+AST+STL+BLK - misses - TOV)", "rules": {"pts": 1.0, "reb": 1.0, "ast": 1.0, "stl": 1.0, "blk": 1.0, "fga": -1.0, "fgm": 1.0, "fta": -1.0, "ftm": 1.0, "tov": -1.0}},
    "stocks_heavy": {"name": "Defense-weighted (generic)", "rules": {"pts": 0.8, "reb": 1.0, "ast": 1.2, "stl": 4.0, "blk": 4.0, "tov": -1.5}},
}


def score(stats: Dict[str, np.ndarray], rules: Dict[str, float]) -> np.ndarray:
    """Score arrays of per-draw (or per-game) stats. Works on any broadcastable arrays."""
    ref = next(iter(stats.values()))
    total = np.zeros_like(np.asarray(ref, dtype=float))
    for k, w in rules.items():
        if k in ("dd", "td"):
            continue
        if k not in stats:
            raise KeyError(f"stat '{k}' not available for scoring")
        total = total + w * np.asarray(stats[k], dtype=float)
    if "dd" in rules or "td" in rules:
        cats = np.stack([np.asarray(stats[c], dtype=float) >= 10 for c in ("pts", "reb", "ast", "stl", "blk")], axis=0).sum(axis=0)
        if "dd" in rules:
            total = total + rules["dd"] * (cats >= 2)
        if "td" in rules:
            total = total + rules["td"] * (cats >= 3)
    return total


class ScoringRegistry:
    def __init__(self, store: Store):
        self.store = store
        self.ensure_presets()

    def ensure_presets(self) -> None:
        for sid, p in PRESETS.items():
            if not self.store.one("SELECT 1 FROM scoring_systems WHERE scoring_id=?", (sid,)):
                self.store.execute("INSERT INTO scoring_systems VALUES (?,?,?,?,?)", (sid, 1, p["name"], json.dumps(p["rules"], sort_keys=True), utcnow()))

    def list(self) -> List[dict]:
        out = []
        for r in self.store.query("SELECT scoring_id, MAX(version) v FROM scoring_systems GROUP BY scoring_id ORDER BY scoring_id"):
            out.append(self.get(r["scoring_id"]))
        return out

    def get(self, scoring_id: str, version: Optional[int] = None) -> dict:
        row = self.store.one("SELECT * FROM scoring_systems WHERE scoring_id=? " + ("AND version=?" if version else "ORDER BY version DESC LIMIT 1"),
                             (scoring_id, version) if version else (scoring_id,))
        if not row:
            raise KeyError(f"unknown scoring system {scoring_id}")
        return {"id": row["scoring_id"], "version": row["version"], "name": row["name"], "rules": json.loads(row["rules_json"]), "created_at": row["created_at"]}

    def add_version(self, scoring_id: str, name: str, rules: Dict[str, float]) -> dict:
        bad = [k for k in rules if k not in STAT_KEYS]
        if bad:
            raise ValueError(f"unsupported stat keys in rules: {bad}")
        latest = self.store.one("SELECT MAX(version) v FROM scoring_systems WHERE scoring_id=?", (scoring_id,))
        v = (latest["v"] or 0) + 1
        self.store.execute("INSERT INTO scoring_systems VALUES (?,?,?,?,?)", (scoring_id, v, name, json.dumps({k: float(x) for k, x in rules.items()}, sort_keys=True), utcnow()))
        return self.get(scoring_id, v)
