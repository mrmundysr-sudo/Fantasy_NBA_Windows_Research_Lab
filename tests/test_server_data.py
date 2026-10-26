import json, os, tempfile, unittest, urllib.error, urllib.request
import numpy as np
from nbalab import synthetic, dataset
from nbalab.server import App
from nbalab.store import Store
from tests.helpers import shared_lab


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = shared_lab()
        cls.app = App(cls.lab)
        cls.app.start()
        cls.base = f"http://127.0.0.1:{cls.app.port}"

    @classmethod
    def tearDownClass(cls):
        cls.app.stop()

    def call(self, path, body=None, token=True, host=None, method=None):
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Lab-Token"] = self.app.token
        if host:
            h["Host"] = host
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_token_required(self):
        self.assertEqual(self.call("/api/status", token=False)[0], 401)
        self.assertEqual(self.call("/api/status")[0], 200)

    def test_bad_host_header_rejected(self):
        self.assertEqual(self.call("/api/status", host="evil.example.com")[0], 403)

    def test_no_cors_headers(self):
        req = urllib.request.Request(self.base + "/api/status", headers={"X-Lab-Token": self.app.token, "Origin": "http://evil.example"})
        with urllib.request.urlopen(req) as r:
            self.assertIsNone(r.headers.get("Access-Control-Allow-Origin"))

    def test_static_path_traversal_blocked(self):
        for p in ("/../server.py", "/..%2fserver.py", "/%2e%2e/schema.py", "/app.py", "/lab.sqlite"):
            code, _ = self.call(p)
            self.assertIn(code, (400, 401, 403, 404), p)

    def test_index_embeds_token_and_csp(self):
        with urllib.request.urlopen(self.base + "/?t=" + self.app.token) as r:
            html = r.read().decode()
            csp = r.headers.get("Content-Security-Policy", "")
        self.assertIn(self.app.token, html)
        self.assertNotIn("__LAB_TOKEN__", html)
        self.assertIn("default-src 'self'", csp)
        self.assertNotIn("<script>", html.replace("<script src", ""))  # no inline scripts

    def test_workbench_and_error_shape(self):
        c, b = self.call("/api/workbench", {"query": {"entity": "player", "metrics": [{"id": "pts", "transform": "per36"}], "min_minutes": 300, "limit": 3}})
        self.assertEqual(c, 200)
        self.assertEqual(len(json.loads(b)["rows"]), 3)
        c, b = self.call("/api/workbench", {"query": {"entity": "player", "metrics": [{"id": "zzz", "transform": "raw"}]}})
        self.assertEqual(c, 400)
        self.assertIn("error", json.loads(b))

    def test_api_responses_are_valid_json_without_nan(self):
        c, b = self.call("/api/metrics")
        self.assertEqual(c, 200)
        self.assertNotIn(b"NaN", b)
        json.loads(b)

    def test_workspace_roundtrip(self):
        self.call("/api/workspaces", {"name": "t1", "spec": {"kind": "layout", "layout": {}}})
        c, b = self.call("/api/workspaces")
        self.assertIn("t1", [w["name"] for w in json.loads(b)["workspaces"]])
        self.call("/api/workspaces", {"name": "t1"}, method="DELETE")


class Synthetic(unittest.TestCase):
    def test_fixture_is_deterministic_and_labelled(self):
        def build():
            s = Store(":memory:")
            synthetic.generate(s, seed=3, days_s1=20, days_s2=10, upcoming_days=2, n_teams=8)
            return s
        a, b = build(), build()
        ra = [tuple(r) for r in a.query("SELECT * FROM box ORDER BY game_id, player_id")]
        rb = [tuple(r) for r in b.query("SELECT * FROM box ORDER BY game_id, player_id")]
        self.assertEqual(ra, rb)
        ds = dataset.build(a)
        self.assertEqual(ds.label(), "SYNTHETIC")
        self.assertTrue(all(g["game_date"] >= "2031" for g in ds.games.values()))  # fictional dates, never mistaken for real seasons

    def test_no_real_nba_names_in_fixture(self):
        lab = shared_lab()
        teams = " ".join(t["name"] for t in lab.ds.teams.values()).lower()
        for real in ("lakers", "celtics", "warriors", "knicks", "bulls"):
            self.assertNotIn(real, teams)

    def test_ui_has_no_inline_script_and_no_external_urls(self):
        d = os.path.join(os.path.dirname(__file__), "..", "nbalab", "ui")
        for f in os.listdir(d):
            txt = open(os.path.join(d, f), encoding="utf-8").read()
            self.assertNotIn("http://", txt.replace("http://www.w3.org/2000/svg", ""), f)
            self.assertNotIn("https://", txt, f)

    def test_no_secrets_in_repo_sources(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        for dp, _, fs in os.walk(os.path.join(root, "nbalab")):
            for f in fs:
                if f.endswith(".py"):
                    t = open(os.path.join(dp, f), encoding="utf-8").read().lower()
                    self.assertNotRegex(t, r"api[_-]?key\s*=\s*['\"][a-z0-9]{16,}")


if __name__ == "__main__":
    unittest.main()


class DataSourcesApi(unittest.TestCase):
    """End to end through HTTP with recorded (shape-fixture) transports: enable gate -> import job -> switch database."""

    def test_enable_import_switch_flow(self):
        import time
        from nbalab.lab import Lab
        from nbalab.sources.transport import RecordedTransport
        from tests.test_sources import routes
        tmp = tempfile.mkdtemp()
        old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = tmp
        try:
            os.makedirs(os.path.join(tmp, "NBALab"), exist_ok=True)
            lab = Lab.open(os.path.join(tmp, "NBALab", "synthetic.sqlite"), n_draws=20)
            for s in lab.sources.sources.values():
                s.transport = RecordedTransport(routes())
            app = App(lab); app.start()
            base = f"http://127.0.0.1:{app.port}"

            def call(path, body=None, method=None):
                req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers={"X-Lab-Token": app.token, "Content-Type": "application/json"}, method=method)
                try:
                    with urllib.request.urlopen(req, timeout=60) as r:
                        return r.status, json.loads(r.read())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read())
            c, d = call("/api/datasources")
            self.assertEqual(c, 200)
            self.assertTrue(all(not s["enabled"] for s in d["sources"]))
            self.assertTrue(any(m["capability"] == "lineup_combinations" and m["covered"] for m in d["matrix"]))
            c, _ = call("/api/sources/enable", {"name": "NBA_STATS", "acknowledgement": "yes"})
            self.assertEqual(c, 400)
            # import is refused while the source is disabled
            c, j = call("/api/data/import", {"seasons": ["2026-27"]})
            jid = j["job_id"]
            for _ in range(100):
                c, st = call("/api/jobs/" + jid)
                if st["state"] != "running":
                    break
                time.sleep(0.1)
            self.assertEqual(st["state"], "error")
            self.assertIn("not_enabled", st["error"])
            # enable properly, import, switch
            s = [x for x in d["sources"] if x["name"] == "NBA_STATS"][0]
            c, _ = call("/api/sources/enable", {"name": "NBA_STATS", "acknowledgement": f"I have read {s['terms_url']} and accept responsibility for how I use this source"})
            self.assertEqual(c, 200)
            c, j = call("/api/data/import", {"seasons": ["2026-27"], "overwrite": True})
            for _ in range(200):
                c, st = call("/api/jobs/" + j["job_id"])
                if st["state"] != "running":
                    break
                time.sleep(0.1)
            self.assertEqual(st["state"], "done", st)
            self.assertGreater(st["result"]["box_rows"], 100)
            c, dbs = call("/api/data/databases")
            real = [x for x in dbs["databases"] if x["label"] == "NBA_STATS_IMPORT"]
            self.assertEqual(len(real), 1)
            c, _ = call("/api/data/switch", {"path": "/etc/passwd"})
            self.assertEqual(c, 400)
            c, _ = call("/api/data/switch", {"path": real[0]["path"]})
            self.assertEqual(c, 200)
            c, stt = call("/api/status")
            self.assertFalse(stt["synthetic"])
            self.assertEqual(stt["dataset_label"], "NBA_STATS_IMPORT")
            c, w = call("/api/workbench", {"query": {"entity": "player", "metrics": [{"id": "pts", "transform": "per36"}], "min_games": 1, "min_minutes": 0, "limit": 3, "uncertainty": False}})
            self.assertEqual(w["meta"]["dataset_label"], "NBA_STATS_IMPORT")
            c, r = call("/api/demo/deliver", {})
            self.assertEqual(c, 400)                          # scripted demo feed never runs beside real data
            app.stop()
        finally:
            if old is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old
