import sqlite3, unittest
import numpy as np
from nbalab.models import DRAW_STATS, unpack_draws
from nbalab.providers.base import RawEvent
from nbalab.scoring import PRESETS
from tests.helpers import fresh_lab, shared_lab

I = DRAW_STATS.index


class Projections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = shared_lab()
        cls.gid = [g for g, x in cls.lab.ds.games.items() if x["status"] == "scheduled"][0]
        g = cls.lab.ds.games[cls.gid]
        cls.team = g["home_team_id"]
        cls.pred = cls.lab.champion_model().predict_team(cls.gid, cls.team, cls.lab.clock(), n_draws=120)

    def test_team_minutes_are_constrained_every_draw(self):
        tot = self.pred.draws[:, :, 0].sum(1)
        for t in tot:
            self.assertTrue(any(abs(t - (240 + 25 * k)) < 0.05 for k in range(0, 4)), t)

    def test_box_identity_holds_in_every_draw(self):
        d = self.pred.draws
        pts = 2 * (d[:, :, I("fgm")] - d[:, :, I("fg3m")]) + 3 * d[:, :, I("fg3m")] + d[:, :, I("ftm")]
        self.assertEqual(float(np.abs(d[:, :, I("pts")] - pts).max()), 0.0)
        self.assertEqual(float(np.abs(d[:, :, I("reb")] - d[:, :, I("oreb")] - d[:, :, I("dreb")]).max()), 0.0)

    def test_distributions_are_ordered_and_minutes_predicted_first(self):
        for p in self.pred.players.values():
            s = p["stats"]["pts"]
            self.assertLessEqual(s["p10"], s["p50"] + 1e-9)
            self.assertLessEqual(s["p50"], s["p90"] + 1e-9)
            self.assertLessEqual(p["min_p10"], p["min_p90"] + 1e-9)
        # points are a function of minutes: more projected minutes -> more projected points on average
        ps = sorted(self.pred.players.values(), key=lambda p: p["min_mean"])
        self.assertGreater(np.mean([p["stats"]["pts"]["mean"] for p in ps[-4:]]), np.mean([p["stats"]["pts"]["mean"] for p in ps[:4]]))

    def test_scoring_presets_are_versioned_and_cover_known_rules(self):
        systems = {s["id"]: s for s in self.lab.scoring.list()}
        for k in PRESETS:
            self.assertIn(k, systems)
            self.assertGreaterEqual(systems[k]["version"], 1)

    def test_stored_projection_label_and_draw_blob(self):
        self.lab.ensure_projections()
        rows = self.lab.projector.latest([self.gid])
        self.assertTrue(rows)
        r = rows[0]
        blob = unpack_draws(r["draws"])
        self.assertEqual(blob.shape[1], len(DRAW_STATS))


class InjuryScenario(unittest.TestCase):
    def test_star_out_raises_teammate_minutes_and_history_is_kept(self):
        lab = fresh_lab()
        gid = [g for g, x in lab.ds.games.items() if x["status"] == "scheduled"][0]
        t = lab.ds.games[gid]["home_team_id"]
        m = lab.champion_model()
        before = m.predict_team(gid, t, lab.clock(), n_draws=60)
        star = max(before.players, key=lambda p: before.players[p]["min_mean"])
        lab.projector.project_team(gid, t)
        n_before = lab.store.one("SELECT COUNT(*) c FROM predictions")["c"]
        ev = RawEvent(source="TEST_FILE", kind="injury", status="out", published_at="2032-12-18T08:00:00Z", team_id=t, player_id=star, game_id=gid, detail="test", data_label="SYNTHETIC")
        res = lab.events.ingest([ev])
        self.assertEqual(res["inserted"], 1)
        after = m.predict_team(gid, t, lab.clock(), n_draws=60)
        self.assertLess(after.players[star]["p_play"], 0.05)
        others = [p for p in before.players if p != star]
        gain = np.mean([after.players[p]["min_mean"] - before.players[p]["min_mean"] for p in others])
        self.assertGreater(gain, 0.3)
        self.assertGreater(lab.store.one("SELECT COUNT(*) c FROM predictions")["c"], n_before)  # rows appended, not overwritten

    def test_future_dated_event_is_invisible_to_earlier_as_of(self):
        lab = fresh_lab()
        gid = [g for g, x in lab.ds.games.items() if x["status"] == "scheduled"][0]
        t = lab.ds.games[gid]["home_team_id"]
        pid = next(p for p, x in lab.ds.players.items() if x["team_id"] == t)
        lab.events.ingest([RawEvent(source="TEST_FILE", kind="injury", status="out", published_at="2032-12-30T00:00:00Z", team_id=t, player_id=pid, game_id=gid, data_label="SYNTHETIC")], recompute=False)
        self.assertNotIn(pid, lab.fb.events.status_asof(gid, "2032-12-18T09:00:00Z"))
        self.assertIn(pid, lab.fb.events.status_asof(gid, "2032-12-31T00:00:00Z"))

    def test_conflicting_reports_latest_published_wins_and_is_flagged(self):
        lab = fresh_lab()
        gid = [g for g, x in lab.ds.games.items() if x["status"] == "scheduled"][0]
        t = lab.ds.games[gid]["home_team_id"]
        pid = next(p for p, x in lab.ds.players.items() if x["team_id"] == t)
        mk = lambda s, at, src: RawEvent(source=src, kind="injury", status=s, published_at=at, team_id=t, player_id=pid, game_id=gid, data_label="SYNTHETIC")
        lab.events.ingest([mk("out", "2032-12-18T05:00:00Z", "A"), mk("available", "2032-12-18T07:00:00Z", "B")], recompute=False)
        self.assertEqual(lab.fb.events.status_asof(gid, "2032-12-18T09:00:00Z")[pid]["status"], "available")
        self.assertTrue(lab.events.conflicts())

    def test_duplicate_report_is_not_double_counted(self):
        lab = fresh_lab()
        gid = [g for g, x in lab.ds.games.items() if x["status"] == "scheduled"][0]
        t = lab.ds.games[gid]["home_team_id"]
        pid = next(p for p, x in lab.ds.players.items() if x["team_id"] == t)
        ev = RawEvent(source="A", kind="injury", status="out", published_at="2032-12-18T05:00:00Z", team_id=t, player_id=pid, game_id=gid, data_label="SYNTHETIC")
        self.assertEqual(lab.events.ingest([ev], recompute=False)["inserted"], 1)
        self.assertEqual(lab.events.ingest([ev], recompute=False)["inserted"], 0)

    def test_unconfigured_providers_claim_no_freshness(self):
        lab = shared_lab()
        for a in lab.events.adapters.values():
            v = a.verify()
            if not v["configured"]:
                self.assertNotEqual(v["mode"], "verified_live")
        for s in lab.events.source_status():
            if s["name"] == "SYN_DEMO_FEED" or s["kind"] == "demo":
                self.assertEqual(s["freshness_claim"], "simulated")

    def test_demo_feed_events_are_labelled_synthetic(self):
        lab = shared_lab()
        for e in lab.events.recent_events(200):
            if e["source"].startswith("SYN"):
                self.assertEqual(e["data_label"], "SYNTHETIC")


class AuditAndImmutability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = shared_lab()
        cls.lab.ensure_projections()

    def test_leakage_probe_passes(self):
        from nbalab import audit
        r = audit.check_leakage(self.lab)
        self.assertEqual(r["status"], "pass", r["differences"])
        self.assertGreater(r["features_compared"], 100)

    def test_leakage_probe_can_fail(self):
        """The probe must detect leakage: sabotage the feature builder to peek at the target game and expect failure."""
        from nbalab import audit, features
        orig = features.History.__dict__.get("state_before") or None
        lab = shared_lab()
        fb = lab.fb
        real = fb.team_features
        gid = [g for g, x in lab.ds.games.items() if x["status"] == "final"][200]

        def leaky(game_id, team_id, as_of=None, events_override=None):
            rows = real(game_id, team_id, as_of, events_override)
            m = lab.ds.pg["game_id"] == game_id
            leak = float(lab.ds.pg["pts"][m].sum())
            for r in rows:
                r["mean5"] = r["mean5"] + leak * 0.001
            return rows
        fb.team_features = leaky
        try:
            r = audit.check_leakage(lab, gid)
        finally:
            fb.team_features = real
        self.assertEqual(r["status"], "fail")

    def test_append_only_tables_reject_update_and_delete(self):
        for table in ("predictions", "scoring_systems", "model_registry"):
            row = self.lab.store.one(f"SELECT rowid r FROM {table} LIMIT 1")
            self.assertIsNotNone(row, table)
            for sql in (f"UPDATE {table} SET rowid=rowid WHERE rowid=?", f"DELETE FROM {table} WHERE rowid=?"):
                with self.assertRaises(sqlite3.DatabaseError):
                    self.lab.store.conn.execute(sql, (row["r"],))
                self.lab.store.conn.rollback()

    def test_scoring_change_creates_new_version(self):
        s0 = self.lab.scoring.get("classic_points")
        r = self.lab.scoring.add_version("classic_points", "Classic tweaked", {**s0.rules, "ast": 1.75} if hasattr(s0, "rules") else s0["rules"])
        self.assertEqual(r["version"], s0.version + 1 if hasattr(s0, "version") else s0["version"] + 1)
        self.assertEqual(self.lab.scoring.get("classic_points", s0.version if hasattr(s0, "version") else s0["version"]) is not None, True)

    def test_full_audit_has_no_failures(self):
        from nbalab import audit
        rep = audit.full_report(self.lab, deep=True)
        self.assertEqual(rep["summary"]["fail"], 0, rep)
        self.assertEqual(rep["dataset_label"], "SYNTHETIC")

    def test_reproducible_data_hash(self):
        from nbalab import audit
        self.assertEqual(audit.data_hash(self.lab.store), audit.data_hash(self.lab.store))


if __name__ == "__main__":
    unittest.main()
