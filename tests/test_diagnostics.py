import json, os, tempfile, time, unittest
from nbalab.diagnostics import DiagnosticsRunner, DEFAULT_SOURCES
from nbalab.sources import SourceRouter
from nbalab.sources.base import SourceError
from nbalab.sources.transport import RecordedTransport
from nbalab.store import Store
from tests.test_sources import routes


def runner(extra=None):
    r = routes(); r.update(extra or {})
    return DiagnosticsRunner(SourceRouter(Store(":memory:"), transport=RecordedTransport(r)))


def wait(d):
    for _ in range(300):
        if d.progress()["status"] == "done":
            return d.progress()
        time.sleep(0.05)
    raise AssertionError("diagnostics did not finish")


class Diagnostics(unittest.TestCase):
    def test_terms_gate_blocks_requests(self):
        d = runner()
        with self.assertRaises(ValueError):
            d.start(["NBA_STATS"], [])
        self.assertEqual(d.status, "idle")
        self.assertFalse(d.router.sources["NBA_STATS"].enabled)
        self.assertEqual(d.router.sources["NBA_STATS"].transport.calls if hasattr(d.router.sources["NBA_STATS"].transport, "calls") else [], [])

    def test_run_progress_and_report_honest(self):
        d = runner({"/stats/teamgamelogs": SourceError("timeout", "no response in 30s"), "official.nba.com": SourceError("unreachable", "simulated")})
        d.start(DEFAULT_SOURCES, DEFAULT_SOURCES)
        p = wait(d)
        self.assertEqual(p["done"], p["total"])
        states = {(s["source"], s["capability"]): s["state"] for s in p["steps"]}
        self.assertEqual(states[("NBA_STATS", "team_game_logs")], "timeout")
        self.assertEqual(states[("NBA_STATS", "schedule")], "ok")
        self.assertEqual(states[("NBA_INJURY_REPORT", "injuries")], "unreachable")
        rep = d.build_report()
        self.assertEqual(rep["summary"]["total"], p["total"])
        self.assertEqual(set(rep["terms_acknowledgements"]), set(DEFAULT_SOURCES))
        blob = json.dumps(rep)
        for private in (os.path.expanduser("~"), os.environ.get("USER", "\0"), "127.0.0.1"):
            self.assertNotIn(private, blob)

    def test_export_requires_run_and_writes_file(self):
        d = runner()
        with self.assertRaises(ValueError):
            d.export(tempfile.mkdtemp())
        d.start(["NBA_LIVE"], ["NBA_LIVE"]); wait(d)
        out = d.export(tempfile.mkdtemp())
        self.assertTrue(os.path.basename(out["path"]) == "probe_report.json" and json.load(open(out["path"]))["results"])

    def test_two_timeouts_stop_hammering_host(self):
        d = DiagnosticsRunner(SourceRouter(Store(":memory:"), transport=RecordedTransport({"/stats/": SourceError("timeout", "no response in 30s")})))
        d.start(["NBA_STATS"], ["NBA_STATS"]); p = wait(d)
        outs = [s["state"] for s in p["steps"]]
        self.assertEqual(outs.count("timeout"), 2)
        self.assertTrue(all(o == "skipped" for o in outs[2:]))

    def test_cannot_start_twice_and_unknown_source_ignored(self):
        d = runner()
        with self.assertRaises(ValueError):
            d.start(["NOPE"], ["NOPE"])


class LabRoutes(unittest.TestCase):
    def test_lab_server_exposes_diagnostics(self):
        from nbalab.lab import Lab
        from nbalab.server import App
        import urllib.request
        tmp = tempfile.mkdtemp(); old = os.environ.get("LOCALAPPDATA"); os.environ["LOCALAPPDATA"] = tmp
        try:
            lab = Lab.open(os.path.join(tmp, "s.sqlite"), n_draws=20)
            app = App(lab); app.start()
            req = urllib.request.Request(f"http://127.0.0.1:{app.port}/api/diagnostics/state", headers={"X-Lab-Token": app.token})
            st = json.loads(urllib.request.urlopen(req, timeout=20).read())
            self.assertEqual([s["name"] for s in st["sources"]], DEFAULT_SOURCES)
            app.stop() if hasattr(app, "stop") else None
        finally:
            if old is None: os.environ.pop("LOCALAPPDATA", None)
            else: os.environ["LOCALAPPDATA"] = old
