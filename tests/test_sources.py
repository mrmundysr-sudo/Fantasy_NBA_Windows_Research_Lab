import json, tempfile, os, unittest
from nbalab.store import Store
from nbalab.sources import SourceRouter, SourceError
from nbalab.sources.nba_stats import NbaStatsSource, NbaLiveSource, STATS_MAP, KNOWN_BROKEN, catalog, parse_minutes
from nbalab.sources.balldontlie import BallDontLieSource
from nbalab.sources.sportsdataio import SportsDataIOSource, sdio_date
from nbalab.sources.transport import RecordedTransport
from nbalab.sources.events_bridge import SourceEventAdapter
from nbalab.sources.importer import import_real
from tests import source_fixtures as F


def routes():
    return {"/stats/playerindex": F.player_index(), "/stats/scheduleleaguev2": F.schedule(), "/stats/playergamelogs": F.player_game_logs(),
            "/stats/teamgamelogs": F.team_game_logs(), "/stats/boxscoretraditionalv3": F.box_score_v3(),
            "todaysScoreboard": F.live_scoreboard(), "boxscore_0022600099": F.live_box(),
            "balldontlie.io/v1/player_injuries": F.bdl_injuries(), "InjuredPlayers": F.sdio_injuries()}


def make_router(tr=None, enable=("NBA_STATS", "NBA_LIVE")):
    store = Store(":memory:")
    tr = tr or RecordedTransport(routes())
    os.environ["BALLDONTLIE_API_KEY"] = "test-key"; os.environ["BALLDONTLIE_TIER"] = "all-star"; os.environ["SPORTSDATAIO_API_KEY"] = "test-key"
    r = SourceRouter(store, transport=tr)
    for n in enable:
        r.enable(n, f"I have read {r.sources[n].terms_url} and accept responsibility for how I use this source")
    return r, tr, store


class Catalog(unittest.TestCase):
    def test_every_mapped_endpoint_and_override_exists_in_catalog(self):
        cat = catalog()["endpoints"]
        for cap, (ep, ov, ds, _n) in STATS_MAP.items():
            self.assertIn(ep, cat, cap)
            for k in ov:
                self.assertIn(k, cat[ep]["parameters"], f"{cap}: {k}")
            if ds:
                self.assertIn(ds, cat[ep]["datasets"], cap)

    def test_catalog_size_and_provenance(self):
        c = catalog()
        self.assertGreaterEqual(len(c["endpoints"]), 130)
        self.assertEqual(c["license"], "MIT")

    def test_known_broken_endpoints_not_used_for_capabilities(self):
        for cap, (ep, *_r) in STATS_MAP.items():
            self.assertNotIn(ep, KNOWN_BROKEN, cap)

    def test_minutes_parsing(self):
        self.assertAlmostEqual(parse_minutes("34:12"), 34.2)
        self.assertAlmostEqual(parse_minutes("PT25M01.00S"), 25 + 1 / 60)
        self.assertEqual(parse_minutes(33.5), 33.5)
        self.assertIsNone(parse_minutes(""))
        self.assertIsNone(parse_minutes(None))
        self.assertIsNone(parse_minutes("DNP"))

    def test_request_building(self):
        s = NbaStatsSource(RecordedTransport())
        url, p = s.build_request("box_score", game_id=22600001)
        self.assertEqual(p["GameID"], "22600001")
        self.assertTrue(url.endswith("/stats/boxscoretraditionalv3"))
        url, p = s.build_request("player_game_logs_advanced", season="2026-27")
        self.assertEqual((p["MeasureType"], p["Season"]), ("Advanced", "2026-27"))
        with self.assertRaises(SourceError):
            s.build_request("player_game_logs", bogus_param=1)

    def test_sportsdata_date_format(self):
        self.assertEqual(sdio_date("2026-10-05"), "2026-OCT-05")


class Gating(unittest.TestCase):
    def test_disabled_by_default_and_ack_must_match(self):
        r, tr, store = make_router(enable=())
        with self.assertRaises(SourceError) as cm:
            r.fetch_from("NBA_STATS", "schedule", season="2026-27")
        self.assertEqual(cm.exception.kind, "not_enabled")
        self.assertEqual(tr.calls, [])                      # nothing was requested
        with self.assertRaises(SourceError):
            r.enable("NBA_STATS", "ok")
        self.assertFalse(r.sources["NBA_STATS"].enabled)

    def test_ack_persists_across_reopen(self):
        r, tr, store = make_router(enable=("NBA_STATS",))
        r2 = SourceRouter(store, transport=tr)
        self.assertTrue(r2.sources["NBA_STATS"].enabled)
        self.assertIn("nba.com/termsofuse", r2.sources["NBA_STATS"].ack["text"])
        self.assertFalse(r2.sources["NBA_LIVE"].enabled)

    def test_keyed_source_needs_env_key(self):
        r, tr, store = make_router(enable=("BALLDONTLIE",))
        os.environ.pop("BALLDONTLIE_API_KEY", None)
        with self.assertRaises(SourceError) as cm:
            r.fetch_from("BALLDONTLIE", "injuries")
        self.assertEqual(cm.exception.kind, "not_configured")
        os.environ["BALLDONTLIE_API_KEY"] = "test-key"

    def test_tier_gate(self):
        r, tr, store = make_router(enable=("BALLDONTLIE",))
        os.environ["BALLDONTLIE_TIER"] = "free"
        r.sources["BALLDONTLIE"].tier = "free"
        with self.assertRaises(SourceError) as cm:
            r.fetch_from("BALLDONTLIE", "injuries")
        self.assertEqual(cm.exception.kind, "auth")
        os.environ["BALLDONTLIE_TIER"] = "all-star"

    def test_key_never_stored_in_log_or_config(self):
        r, tr, store = make_router(enable=("BALLDONTLIE",))
        r.fetch_from("BALLDONTLIE", "injuries")
        dump = json.dumps([dict(x) for x in store.query("SELECT * FROM source_log")]) + json.dumps(store.get_meta("source_config"))
        self.assertNotIn("test-key", dump)


class Normalizers(unittest.TestCase):
    def test_nba_player_game_logs(self):
        r, tr, _ = make_router()
        f = r.fetch("player_game_logs", season="2026-27")
        self.assertGreater(len(f.records), 100)
        x = f.records[0]
        for k in ("game_id", "game_date", "player_id", "team_abbr", "opp_abbr", "home", "min", "pts", "pm"):
            self.assertIn(k, x)
        self.assertEqual(x["game_date"], x["game_date"][:10])
        self.assertIsInstance(x["home"], bool)
        pts = 2 * (x["fgm"] - x["fg3m"]) + 3 * x["fg3m"] + x["ftm"]
        self.assertEqual(pts, x["pts"])
        self.assertEqual(tr.calls[0][1]["Season"], "2026-27")
        self.assertIn("Referer", tr.calls[0][2])

    def test_box_score_starters_and_dnp_comment(self):
        r, tr, _ = make_router()
        recs = r.fetch("box_score", game_id="0022600001").records
        self.assertEqual(sum(1 for x in recs if x["started"] and x["team_abbr"] == "AAA"), 5)
        dnp = [x for x in recs if x["comment"]]
        self.assertTrue(dnp and all(x["min"] is None for x in dnp))
        self.assertAlmostEqual(next(x for x in recs if x["min"])["min"], 30.5)

    def test_schedule_status(self):
        r, *_ = make_router()
        recs = r.fetch("schedule", season="2026-27").records
        self.assertEqual({x["status"] for x in recs}, {"final", "scheduled"})
        self.assertEqual(recs[0]["game_id"], "0022600001")

    def test_live_starters_and_inactives(self):
        r, tr, _ = make_router()
        st = r.fetch_from("NBA_LIVE", "starting_lineups", game_id="0022600099").records
        self.assertEqual(len(st), 10)
        ina = r.fetch_from("NBA_LIVE", "injuries", game_id="0022600099").records
        self.assertTrue(ina and ina[0]["reason"].startswith("INACTIVE"))

    def test_vendor_injury_status_mapping(self):
        r, *_ = make_router(enable=("BALLDONTLIE", "SPORTSDATAIO"))
        b = r.fetch_from("BALLDONTLIE", "injuries").records[0]
        self.assertEqual(b["status"], "questionable")
        s = r.fetch_from("SPORTSDATAIO", "injuries").records
        self.assertEqual(len(s), 1)                         # healthy player filtered
        self.assertEqual(s[0]["status"], "out")


class Routing(unittest.TestCase):
    def test_fallback_when_first_source_blocked(self):
        tr = RecordedTransport({"/stats/": SourceError("blocked", "HTTP 403", 403), "sportsdata": F.sdio_injuries(), "InjuredPlayers": F.sdio_injuries()})
        r, tr, store = make_router(tr, enable=("NBA_STATS", "SPORTSDATAIO"))
        f = r.fetch("injuries")
        self.assertEqual(f.source, "SPORTSDATAIO")
        r.priority["player_game_logs"] = ["NBA_STATS"]
        with self.assertRaises(SourceError):
            r.fetch("player_game_logs", season="2026-27")
        log = store.query("SELECT * FROM source_log WHERE ok=0")
        self.assertTrue(any(x["error_kind"] == "blocked" for x in log))

    def test_fetch_all_combines_sources(self):
        r, *_ = make_router(enable=("BALLDONTLIE", "SPORTSDATAIO"))
        res = r.fetch_all("injuries")
        self.assertEqual({f.source for f in res}, {"BALLDONTLIE", "SPORTSDATAIO"})

    def test_probe_records_honest_outcomes_and_updates_matrix(self):
        blocked = RecordedTransport({"/stats/": SourceError("timeout", "no response in 30s"), "cdn.nba.com": SourceError("blocked", "HTTP 403", 403)})
        r, tr, store = make_router(blocked)
        res = r.probe()
        outs = {(x["source"], x["capability"]): x["outcome"] for x in res}
        self.assertEqual(outs[("NBA_STATS", "schedule")], "timeout")
        self.assertEqual(outs[("NBA_LIVE", "live_scoreboard")], "blocked")
        self.assertEqual(outs[("BALLDONTLIE", "(all)")], "not_enabled")
        mx = {m["capability"]: m for m in r.matrix()}
        cell = next(c for c in mx["schedule"]["sources"] if c["source"] == "NBA_STATS")
        self.assertEqual(cell["evidence"], "library_catalog")           # a failed probe never upgrades evidence
        # now a working network
        r2, tr2, store2 = make_router()
        res2 = r2.probe(["NBA_STATS"])
        ok = {x["capability"] for x in res2 if x["outcome"] == "ok"}
        self.assertTrue({"schedule", "players_index", "player_game_logs", "box_score"} <= ok)
        cell = next(c for c in {m["capability"]: m for m in r2.matrix()}["player_game_logs"]["sources"] if c["source"] == "NBA_STATS")
        self.assertEqual(cell["evidence"], "probe_verified")

    def test_matrix_covers_user_priorities(self):
        r, *_ = make_router()
        mx = {m["capability"]: m for m in r.matrix()}
        for cap in ("player_game_logs", "lineup_combinations", "shot_locations", "player_season_advanced", "team_season_advanced", "injuries", "starting_lineups", "box_score"):
            self.assertTrue(mx[cap]["covered"], cap)


class Bridge(unittest.TestCase):
    def test_events_have_honest_timestamps(self):
        r, *_ = make_router(enable=("BALLDONTLIE", "SPORTSDATAIO"))
        sd = SourceEventAdapter(r, "SPORTSDATAIO", "injuries").to_events(r.fetch_from("SPORTSDATAIO", "injuries").records, "2026-10-25T12:00:00Z")
        self.assertEqual(sd[0].data_label, "LIVE_RETRIEVED_TIME")       # Updated had no time zone -> we do not guess one
        self.assertEqual(sd[0].published_at, "2026-10-25T12:00:00Z")
        self.assertEqual(sd[0].status, "out")
        bd = SourceEventAdapter(r, "BALLDONTLIE", "injuries").to_events(r.fetch_from("BALLDONTLIE", "injuries").records, "2026-10-25T12:00:00Z")
        self.assertEqual(bd[0].status, "questionable")

    def test_disabled_source_adapter_is_not_configured(self):
        from nbalab.providers.base import NotConfigured
        r, *_ = make_router(enable=())
        ad = SourceEventAdapter(r, "SPORTSDATAIO", "injuries")
        self.assertFalse(ad.configured())
        with self.assertRaises(NotConfigured):
            ad.fetch()


class Importer(unittest.TestCase):
    def test_import_builds_non_synthetic_dataset_with_missing_tracking(self):
        from nbalab import dataset
        import math
        r, *_ = make_router()
        s = Store(":memory:")
        seen = []
        out = import_real(r, s, ["2026-27"], progress=seen.append)
        self.assertGreater(out["box_rows"], 100)
        ds = dataset.build(s)
        self.assertFalse(ds.synthetic)
        self.assertEqual(ds.label(), "NBA_STATS_IMPORT")
        self.assertTrue(math.isnan(float(ds.pg["touches"][0])))          # missing, not zero
        self.assertEqual(sum(1 for g in ds.games.values() if g["status"] == "scheduled"), 2)
        info = s.get_meta("import_info")
        self.assertTrue(info["started_inferred"])
        # exactly five inferred starters per team-game
        import numpy as np
        for gid in list(ds.games)[:3]:
            for t in (ds.games[gid]["home_team_id"], ds.games[gid]["away_team_id"]):
                m = (ds.pg["game_id"] == gid) & (ds.pg["team_id"] == t)
                if m.any():
                    self.assertEqual(int(ds.pg["started"][m].sum()), 5)

    def test_engine_runs_on_imported_data_and_respects_nan(self):
        from nbalab import dataset
        from nbalab.engine import Engine
        r, *_ = make_router()
        s = Store(":memory:")
        import_real(r, s, ["2026-27"])
        eng = Engine(dataset.build(s))
        res = eng.run({"entity": "player", "metrics": [{"id": "pts", "transform": "per36"}, {"id": "ts_pct", "transform": "raw"}], "min_games": 1, "min_minutes": 0, "limit": 5, "uncertainty": False})
        self.assertEqual(res["meta"]["dataset_label"], "NBA_STATS_IMPORT")
        self.assertFalse(res["meta"]["synthetic"])
        self.assertEqual(len(res["rows"]), 5)

    def test_final_games_without_box_rows_are_dropped_not_zero_filled(self):
        r, *_ = make_router()
        s = Store(":memory:")
        tr = RecordedTransport({**routes(), "/stats/playergamelogs": {"resultSets": [{"name": "PlayerGameLogs", "headers": F._CAT["playergamelogs"]["datasets"]["PlayerGameLogs"], "rowSet": []}]}})
        r2, *_ = make_router(tr)
        out = import_real(r2, s, ["2026-27"])
        self.assertEqual(out["games"], 2)                                # only the scheduled ones survive
        self.assertEqual(out["skipped"]["final_games_without_complete_box"], 12)


if __name__ == "__main__":
    unittest.main()
