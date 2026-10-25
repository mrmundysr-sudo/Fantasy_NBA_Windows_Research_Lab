"""Headless Chromium UI smoke test: opens every panel, exercises key actions, fails on console errors."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from playwright.sync_api import sync_playwright
from nbalab.lab import Lab
from nbalab.server import App

def main(shots=None):
    lab = Lab.open(os.path.join(tempfile.mkdtemp(), "ui.sqlite")); app = App(lab); app.start()
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 900})
        pg.on("console", lambda m: errors.append("console:" + m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append("pageerror:" + str(e)))
        pg.goto(app.url); pg.wait_for_selector("#launchers button"); pg.wait_for_selector("#desk table", timeout=60000)
        def shot(n):
            if shots: pg.screenshot(path=os.path.join(shots, n + ".png"))
        shot("picks")
        pg.click("#desk table tbody tr"); pg.wait_for_timeout(500); shot("picks_detail")
        for pid in ["workbench", "research", "monitor", "models", "audit", "predlab"]:
            pg.click(f'#launchers button[data-id="{pid}"]'); pg.wait_for_timeout(700)
        pg.click("#tile"); pg.wait_for_timeout(300)
        pg.click("#wb-run"); pg.wait_for_selector(".panel:has-text('Workbench') table.t tbody tr", timeout=60000); shot("workbench")
        pg.click(".panel:has-text('Workbench') table.t tbody tr"); pg.wait_for_selector(".modal", timeout=20000); shot("support"); pg.keyboard.press("Escape")
        if pg.query_selector(".modal-bg"): pg.mouse.click(5, 5)
        ta = pg.query_selector(".panel:has-text('Natural-Language') textarea"); ta.fill("Top 5 players by points per 36 minutes with at least 300 minutes")
        pg.click(".panel:has-text('Natural-Language') button.primary >> text=Ask"); pg.wait_for_selector("text=Query Inspector", timeout=60000); shot("nlq")
        pg.click(".panel:has-text('Live Source Monitor') button:has-text('Deliver next demo report')"); pg.wait_for_timeout(8000); shot("monitor")
        pg.click(".panel:has-text('Audit') button:has-text('Run deep audit')"); pg.wait_for_selector(".panel:has-text('Audit') >> text=Leakage", timeout=90000); shot("audit")
        pg.fill("#gsearch", "a"); pg.wait_for_timeout(800); pg.click("#tile"); pg.wait_for_timeout(300); shot("tiled")
        b.close()
    app.stop()
    if errors:
        print("UI ERRORS:"); [print(" ", e) for e in errors]; return 1
    print("UI smoke OK"); return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
