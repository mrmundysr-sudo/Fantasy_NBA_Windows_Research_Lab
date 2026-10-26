# Installing and running the NBA Research Lab (private build)

**Status: research prototype. All bundled data is SYNTHETIC (fictional teams and players). Nothing here is a real NBA pick.**

## Option A: portable build from CI (no Python needed)
*Currently blocked: the CI artifact upload hit a GitHub artifact-storage quota (see `STATUS.md`). Re-run the workflow after the quota resets or after clearing old artifacts, or use Option B.*

1. In GitHub, open **Actions → windows-build →** latest run on `dev/windows-research-lab`.
2. Download the artifact **NBALab-windows-portable** (private repo; you must be signed in).
3. Unzip and run `NBALab\NBALab.exe`. Windows SmartScreen may warn because the build is unsigned: *More info → Run anyway*.
4. A desktop window opens (needs the Microsoft Edge WebView2 runtime, present on current Windows 10/11). If it is missing the app opens your default browser instead.
5. Data lives in `%LOCALAPPDATA%\NBALab\lab.sqlite`. Delete that file to reset to a fresh synthetic fixture.

Flags: `--browser` (use default browser), `--no-open` (serve only), `--db PATH`, `--selftest` (headless smoke check, exits 0/1).

## Option B: from source
```powershell
py -3.12 -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements-desktop.txt
python -m nbalab            # desktop window
python -m nbalab --browser  # browser tab
```
Tests: `pip install pytest` then `python -m pytest -q tests`. UI smoke test: `pip install playwright; python -m playwright install chromium; python tools/ui_smoke.py`.
Build the exe yourself: `pip install -r requirements-build.txt; pyinstaller packaging/nbalab.spec --noconfirm` (output `dist\NBALab`).

## First run
Opening takes a few seconds while the synthetic fixture is generated, models are trained and the first projections are made. Start with the **Picks** panel, then try **Ask** (natural-language) and **Workbench**. The **Monitor** panel's *Deliver next demo report* shows an injury report arriving and projections being recomputed.

## Secrets
No API keys are required or included. If provider adapters are enabled later, keys come from environment variables and must never be committed (`.gitignore` already excludes `.env`, `*.key`, `secrets.*`, `*.sqlite`).

## One-click source test (no Python, no terminal)

1. On GitHub open the repository, then **Releases**, then **NBA Source Diagnostics (latest dev build)** (it is private; you need to be signed in).
2. Download `NBA-Source-Diagnostics.exe` and double-click it. No installer, no administrator rights. Windows may show a SmartScreen notice because the file is not code-signed: choose *More info*, then *Run anyway*.
3. Your browser opens a local page (it only listens on your own computer). Read each source's terms link, tick the confirmation, press **Run Tests**.
4. Press **Export Report**. It saves `probe_report.json` in your Downloads folder. Send me that file.

The same tool is inside the Lab as the **Diagnostics** panel and works for any provider added to the source layer.

## Real data (optional, your own machine)

1. Open the Sources panel, read each provider's terms link, and enable only what you accept.
2. Probe: `python tools/probe_sources.py --acknowledge-terms --sources NBA_STATS NBA_LIVE NBA_INJURY_REPORT`.
3. The injury report needs `pip install pypdf`. Keyed sources use the `BALLDONTLIE_API_KEY` / `SPORTSDATAIO_API_KEY` environment variables.
4. Real imports go to a separate database labelled `NBA_STATS_IMPORT`; the synthetic fixture is never mixed in.
