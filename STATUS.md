# Status report: Windows Research Lab, first build

Branch: `dev/windows-research-lab` (not merged to `main`, nothing published). Date: 2026-10-25. Version 0.1.0.

## One-paragraph summary
A working, launchable, offline-first Windows research workstation exists: a Python/numpy backend, SQLite store, local JSON API and a multi-panel desktop UI covering the five experiences in `PROJECT_VISION.md`. **Every number it shows comes from a clearly labelled SYNTHETIC fixture**, because no real data provider has been evaluated, approved or purchased. It therefore proves the *machinery* (minutes-first modelling, event-driven recomputation, safe analytics, leakage and immutability audits), **not** any real-world predictive skill.

## What is real (implemented and verified here)
- **Metric registry**, 76 metrics tagged observed / derived / inferred / projected / simulated / unavailable (8 explicit "unavailable" with reasons, e.g. tracking-only measures). Ratios are computed from window sums; formulas are versioned (`f1`).
- **Analytics engine**: players, teams, coaches and starting lineups; season/date/last-N windows; home, result, back-to-back, rest, OT, started, teammates in/out, opponent and coach filters; per-game/per-36/per-100 transforms; custom formulas (AST-whitelisted, no code execution); weighted z-score composites; Pearson/Spearman correlation with exact p-values; percentile comparison; seeded bootstrap 90% intervals; sample-size guards; "show supporting games" drill-down.
- **Natural-language research**: deterministic grammar to a structured query, Query Inspector (interpretation, filters, formulas, joins, sources, sample size, uncertainty, supporting games), editable re-run. Refuses ambiguous names, unavailable metrics and anything unmappable (including raw SQL). No external language model.
- **Minutes-first projections**: ridge minutes model, probability of playing, per-minute rate model, team-minute-constrained Monte Carlo (every draw sums to 240 + 25 per OT; box-score identities hold in every draw), with p10-p90 distributions, double-double probability and "why these minutes" drivers.
- **Fantasy rankings**: four scoring presets, user edits create new immutable versions; rank by expected, upside, consistency, minutes opportunity, matchup, defence, injury opportunity, value (needs user-imported costs; otherwise shown as unavailable).
- **Event pipeline**: provider adapters, freshness claims, conflict handling (latest published wins, flagged), stale detection, duplicate suppression, recompute on new reports (demonstrated: a star ruled out raises teammates' projected minutes by about 3.4 on average in the demo). Events dated after a projection's as-of time cannot influence it.
- **Model tournament**: walk-forward evaluation vs four baselines, MAE/RMSE/bias, Brier, interval coverage, reliability, slices (role, injury context, back-to-back, matchup, team, coach), paired-bootstrap promotion rule, champion registry with rollback.
- **Audit**: data-quality checks, a leakage probe (corrupts all future rows, then requires identical pre-game features; a test proves the probe can fail), append-only triggers on predictions/snapshots/scoring/model registry, reproducibility hash.
- **UI**: resizable, movable, tileable panels; global search; saved layouts and saved queries; SVG charts (scatter with fit line, bars with intervals, histogram, line, box); CSV export; permanent SYNTHETIC badge.
- **Security posture (local)**: loopback only, per-launch token, Host-header check, no CORS, strict CSP, no inline scripts, static file allow-list.
- **Tests**: 54 unit/integration tests, all passing on Linux in the development sandbox; headless-Chromium UI smoke test with zero console errors; `--selftest` for the packaged app.

## Demonstrated, with caveats
- **Tournament on synthetic data**: the lab model beat all baselines on minutes MAE (about 3.57 vs 3.79-3.93) and fantasy MAE (about 6.27 vs 6.35-6.50) with 80% interval coverage close to nominal. The generator was written with structure the model can find, so **this is a pipeline check, not evidence of skill on real games**. The promotion rule correctly refused to promote the blend variant, whose gain was within noise.
- **Demo "live" feed**: a scripted SIMULATED feed shows a report arriving and projections updating. It is labelled `simulated` everywhere.

## Incomplete
- No real data adapter is mapped. `HttpJsonProvider` and three placeholder adapters exist but are disabled and unconfigured.
- Value ranking needs a user cost import; no real salary data.
- Lineup metrics are starting lineups only (no play-by-play on-court data).
- Defensive-specialist and matchup categories rely on box-score proxies and an inferred style model; tracking-based defence is unavailable.
- Prediction Lab panel is basic (linear score comparison per player).
- No installer signing; Inno Setup script is untested.
- No scheduled background refresh of sources.
- Hand-written UI has been exercised by a smoke test only, not by a human on a real Windows machine.

## Blocked / needs owner action
- **Real data**: which provider(s), and approval before any purchase, key or terms acceptance. See `PUBLIC_RELEASE_REVIEW.md` (all provider rows Pending).
- **Code signing** certificate if the app is ever distributed beyond the owner.
- **Merge to `main` and any publication**: requires owner approval; not done.

## Data providers (2026-10-25 update, priority: NBA.com/stats + nba_api first)

* Capability-based source layer with interchangeable adapters: `NBA_STATS`, `NBA_LIVE`, `NBA_INJURY_REPORT` (official PDF, new), `BALLDONTLIE`, `SPORTSDATAIO`. All disabled until you type the terms acknowledgement; keys come from environment variables; nothing purchased.
* **Live evidence so far (Windows cloud runner):** cdn.nba.com live scoreboard, box score and starters worked (7 games, 29 and 10 rows). stats.nba.com timed out on all 9 probes, as nba_api warns for cloud IPs. Run the probe on your own machine: `python tools/probe_sources.py --acknowledge-terms --sources NBA_STATS NBA_LIVE NBA_INJURY_REPORT` (or the Sources panel).
* Not verified live: stats.nba.com payloads, the injury-report PDF layout (needs `pip install pypdf`), BALLDONTLIE and SportsDataIO (no keys).
* Not yet built: confirmed starters as a minutes feature, per-game starter backfill, `id_map` use, scheduled refresh.
* One Windows UI-smoke failure occurred on a manual re-run of a commit whose push run passed (flaky); local smoke passes. Watch for recurrence.
* See `docs/DATA_PROVIDER_EVALUATION.md` and `docs/LICENSING_AND_COMMERCIAL_CONCERNS.md`. The NBA.com terms question (Section 9) is an open owner decision.

* **Data Source Diagnostics** (new): standalone `NBA-Source-Diagnostics.exe` (one file, windowed, no admin) plus a Lab panel, sharing `nbalab/diagnostics.py`. Terms are confirmed in the page before any request. Built and attached to the private release `diagnostics-latest` by the `diagnostics-exe` CI job. The page is tested headlessly against recorded fixtures; **a human double-click on a real Windows desktop has not been observed yet**.

## Windows verification
Verified on a GitHub Actions `windows-latest` runner (run 40247859624, commit c737f1f, branch `dev/windows-research-lab`). Passed there: the full test suite (pytest), the headless-Chromium UI smoke test, the PyInstaller build, and `NBALab.exe --selftest` on the packaged app (serves the UI, token check, workbench query, picks projection). Earlier runs failed on two real packaging bugs (relative-import entry point; UI data folder shadowing the package), both fixed.
**Not verified:** nobody has yet launched the desktop window (pywebview/WebView2) on a real Windows desktop, so the visible window, SmartScreen behaviour and layout on real displays are untested. CI only exercises the headless server/browser path.
**Blocked:** the final step, uploading the portable zip as a workflow artifact, fails with *"Artifact storage quota has been hit"* (GitHub account-level; usage recalculates every 6-12 hours or can be cleared in repo/account settings). The step is now non-blocking so it cannot hide test or build results. Until it succeeds, build from source (see `INSTALL.md`).

## Environment notes
- PyPI was unreachable from the development sandbox (HTTP 403), so the build uses only numpy and the standard library; `pytest`, `pywebview` and `PyInstaller` were not installable there and are installed by CI.

## Recommended next experiments
1. Choose and evaluate one real injury/lineup source (terms first), map it into `HttpJsonProvider`, and run in `shadow` mode alongside the demo feed to measure report latency.
2. Replace the synthetic fixture with a legitimately licensed historical box-score set and re-run the tournament chronologically; expect results far smaller than the synthetic gains.
3. Minutes model: add blowout/garbage-time risk from live-pace spreads, and test a gradient-boosted alternative against `lab_v1` in the tournament.
4. Calibrate p(play) from report-status history rather than the hand-set status table.
5. Add on-court lineup data (if a licensed source exists) to unlock true on/off and lineup metrics.
6. Backtest the scoring-system value rankings against realised results to quantify how much the upside and consistency categories add over expected value.
