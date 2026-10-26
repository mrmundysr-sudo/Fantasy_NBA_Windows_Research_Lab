"""Headless browser test of the Diagnostics page with a RECORDED transport (shape fixtures, no network). Usage: python tools/diag_ui_smoke.py [shots_dir]"""
import json, os, sys, tempfile
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from playwright.sync_api import sync_playwright
from nbalab.diag_app import DiagServer
from nbalab.diagnostics import DiagnosticsRunner
from nbalab.sources import SourceRouter
from nbalab.sources.base import SourceError
from nbalab.sources.transport import RecordedTransport
from nbalab.store import Store
from tests.test_sources import routes


def main():
    shots = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
    os.makedirs(shots, exist_ok=True)
    home = tempfile.mkdtemp(); os.makedirs(os.path.join(home, "Downloads"))
    os.environ["HOME"] = os.environ["USERPROFILE"] = home
    r = routes(); r["/stats/teamgamelogs"] = SourceError("timeout", "no response in 30s (host may not answer this network)"); r["official.nba.com"] = SourceError("unreachable", "simulated")
    srv = DiagServer(DiagnosticsRunner(SourceRouter(Store(":memory:"), transport=RecordedTransport(r))))
    srv.start()
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1000, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto(srv.url); pg.wait_for_selector(".src")
        assert pg.is_disabled("#run"), "Run must be disabled until terms are confirmed"
        assert pg.locator(".src").count() == 3
        pg.screenshot(path=os.path.join(shots, "diag_1_terms.png"))
        for n in ("NBA_STATS", "NBA_LIVE", "NBA_INJURY_REPORT"):
            pg.check(f"#ack-{n}")
        assert not pg.is_disabled("#run")
        pg.click("#run"); pg.wait_for_selector("#done:not([hidden])", timeout=60000)
        pg.screenshot(path=os.path.join(shots, "diag_2_results.png"), full_page=True)
        txt = pg.inner_text("#steps")
        assert "Connected" in txt and "Timed out" in txt, txt[:500]
        pg.click("#exp"); pg.wait_for_selector("text=Saved:")
        path = os.path.join(home, "Downloads", "probe_report.json")
        assert os.path.exists(path), pg.inner_text("#where")
        rep = json.load(open(path, encoding="utf-8")); assert rep["summary"]["total"] >= 14 and rep["terms_acknowledgements"]
        assert "127.0.0.1" not in json.dumps(rep)
        b.close()
    assert not errs, errs
    print("DIAG UI OK", rep["summary"]["by_outcome"], "report:", path)


if __name__ == "__main__":
    main()
