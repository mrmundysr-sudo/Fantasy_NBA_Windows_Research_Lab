import math, unittest
import numpy as np
from nbalab import registry
from nbalab.engine import MetricError, QueryError
from tests.helpers import shared_lab


def q(**kw):
    base = {"entity": "player", "metrics": [{"id": "pts", "transform": "per36"}], "min_minutes": 300, "limit": 400, "uncertainty": False}
    base.update(kw)
    return base


class Engine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = shared_lab()
        cls.ds = cls.lab.ds

    def test_ratio_is_sum_ratio_not_average_of_ratios(self):
        res = self.lab.engine.run(q(metrics=[{"id": "ts_pct", "transform": "raw"}]))
        row = res["rows"][0]
        m = self.ds.pg["player_id"] == row["id"]
        pts, fga, fta = self.ds.pg["pts"][m].sum(), self.ds.pg["fga"][m].sum(), self.ds.pg["fta"][m].sum()
        self.assertAlmostEqual(row["values"]["TS%"], 100 * pts / (2 * (fga + 0.44 * fta)), places=3)

    def test_per36_matches_manual(self):
        row = self.lab.engine.run(q())["rows"][0]
        m = self.ds.pg["player_id"] == row["id"]
        self.assertAlmostEqual(row["values"]["Points (per 36)"], 36 * self.ds.pg["pts"][m].sum() / self.ds.pg["min"][m].sum(), places=3)

    def test_unavailable_and_unknown_metrics_refused(self):
        un = [m for m in registry.all_metrics() if m.kind == "unavailable"][0]
        with self.assertRaises((MetricError, QueryError)):
            self.lab.engine.run(q(metrics=[{"id": un.id, "transform": "raw"}]))
        with self.assertRaises((MetricError, QueryError)):
            self.lab.engine.run(q(metrics=[{"id": "does_not_exist", "transform": "raw"}]))

    def test_invalid_transform_refused(self):
        with self.assertRaises((MetricError, QueryError)):
            self.lab.engine.run(q(metrics=[{"id": "ts_pct", "transform": "per36"}]))

    def test_custom_formula_and_bad_formula(self):
        r = self.lab.engine.run(q(metrics=[{"id": "pts", "transform": "per36"}, {"id": "ast", "transform": "per36"}], custom=[{"name": "PA", "formula": "pts_per36 + ast_per36"}]))
        v = r["rows"][0]["values"]
        self.assertAlmostEqual(v["PA"], v["Points (per 36)"] + v["Assists (per 36)"], places=4)
        with self.assertRaises((QueryError, MetricError, ValueError)):
            self.lab.engine.run(q(custom=[{"name": "bad", "formula": "__import__('os')"}]))

    def test_filters_change_sample_and_home_away_partition(self):
        allg = self.lab.engine.run(q(min_minutes=0, min_games=1, metrics=[{"id": "gp", "transform": "raw"}]))["meta"]["rows_matched"]
        h = self.lab.engine.run(q(min_minutes=0, min_games=1, metrics=[{"id": "gp", "transform": "raw"}], filters={"home": True}))["meta"]["rows_matched"]
        a = self.lab.engine.run(q(min_minutes=0, min_games=1, metrics=[{"id": "gp", "transform": "raw"}], filters={"home": False}))["meta"]["rows_matched"]
        self.assertEqual(h + a, allg)
        self.assertGreater(h, 0)

    def test_sample_guard_excludes_small_samples(self):
        loose = self.lab.engine.run(q(min_minutes=0, min_games=1))["meta"]["entities_matched"]
        strict = self.lab.engine.run(q(min_minutes=1500, min_games=1))["meta"]["entities_matched"]
        self.assertLess(strict, loose)

    def test_support_rows_reproduce_aggregate(self):
        res = self.lab.engine.run(q(metrics=[{"id": "pts", "transform": "per_game"}]))
        row = res["rows"][0]
        s = self.lab.engine.support(res["query"], row["id"], 500)
        pts = [g["stats"].get("PTS", g["stats"].get("pts")) for g in s["rows"]]
        self.assertEqual(s["games_total"], row["games"])
        self.assertAlmostEqual(sum(pts) / len(pts), row["values"]["Points (per game)"], places=3)

    def test_correlation_is_valid_and_symmetric(self):
        res = self.lab.engine.run(q(metrics=[{"id": "pts", "transform": "per36"}, {"id": "ast", "transform": "per36"}, {"id": "usg_pct", "transform": "raw"}], correlate={"metrics": ["pts_per36", "ast_per36", "usg_pct"], "level": "entity", "method": "pearson"}))
        r = np.array(res["correlation"]["r"], dtype=float)
        self.assertTrue(np.allclose(r, r.T, equal_nan=True))
        self.assertTrue(np.all(np.abs(r[~np.isnan(r)]) <= 1 + 1e-9))
        self.assertTrue(np.allclose(np.diag(r), 1.0))

    def test_composite_zscore_collinearity_note(self):
        res = self.lab.engine.run(q(metrics=[{"id": "pts", "transform": "per36"}, {"id": "usg_pct", "transform": "raw"}], composite={"name": "C", "weights": {"pts_per36": 1, "usg_pct": 1}}))
        self.assertIn("C", res["rows"][0]["values"])

    def test_bootstrap_ci_is_deterministic_and_brackets_value(self):
        a = self.lab.engine.run(q(uncertainty=True, limit=5))
        b = self.lab.engine.run(q(uncertainty=True, limit=5))
        self.assertEqual(a["rows"][0]["ci90"], b["rows"][0]["ci90"])
        lo, hi = a["rows"][0]["ci90"]["Points (per 36)"]
        self.assertLessEqual(lo, a["rows"][0]["values"]["Points (per 36)"] + 1e-9)
        self.assertGreaterEqual(hi, a["rows"][0]["values"]["Points (per 36)"] - 1e-9)

    def test_dataset_is_labelled_synthetic(self):
        r = self.lab.engine.run(q(limit=1))
        self.assertEqual(r["meta"]["dataset_label"], "SYNTHETIC")


class NLQ(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = shared_lab()

    def test_answer_has_inspector_and_headline(self):
        r = self.lab.nlq.answer("Top 5 players by points per 36 minutes with at least 300 minutes")
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["result"]["rows"]), 5)
        for k in ("interpretation", "formulas", "joins", "sources", "sample_size", "uncertainty", "structured_query", "warnings"):
            self.assertIn(k, r["inspector"], k)
        self.assertTrue(r["headline"])

    def test_unmappable_question_is_refused_not_invented(self):
        r = self.lab.nlq.answer("who will win the championship next year")
        self.assertFalse(r["ok"])
        self.assertTrue(r["suggestions"])

    def test_raw_sql_is_never_executed(self):
        r = self.lab.nlq.answer("SELECT * FROM box; DROP TABLE box")
        self.assertFalse(r["ok"])
        self.assertGreater(self.lab.store.one("SELECT COUNT(*) c FROM box")["c"], 1000)

    def test_unavailable_metric_refused_with_reason(self):
        un = [m for m in registry.all_metrics() if m.kind == "unavailable"][0]
        r = self.lab.nlq.answer(f"top 5 players by {un.name}")
        self.assertFalse(r["ok"])

    def test_edit_and_rerun_roundtrip(self):
        r = self.lab.nlq.answer("Top 5 players by points per 36 minutes with at least 300 minutes")
        sq = dict(r["inspector"]["structured_query"]); sq["limit"] = 3
        r2 = self.lab.nlq.rerun(sq)
        self.assertEqual(len(r2["result"]["rows"]), 3)

    def test_named_player_resolves(self):
        name = list(self.lab.ds.players.values())[30]["name"]
        r = self.lab.nlq.answer(f"{name} points per game this season")
        self.assertTrue(r["ok"], r.get("message"))
        self.assertEqual(r["result"]["rows"][0]["name"], name)


if __name__ == "__main__":
    unittest.main()
