# Data-provider evaluation (priority: NBA.com/stats + nba_api first)

Written 2026-10-25 on branch `dev/windows-research-lab`. **Read this first: no endpoint has been verified live.** Every provider host is blocked
by the build sandbox's egress proxy, so everything below is labelled by the strength of its evidence:

| Evidence level | Meaning |
|---|---|
| `probe_verified` | The in-app probe actually fetched and parsed it (none yet; produced on your machine or in CI) |
| `library_catalog` | Present in the nba_api v1.12.0 endpoint catalogue (139 stats endpoints, vendored as `nbalab/sources/nba_stats_catalog.json`) |
| `documented` | Stated by the vendor's own documentation or pricing page |
| `unverified` | Anything else |

A failed probe never upgrades evidence. Licensing and commercial-release concerns are kept in **`LICENSING_AND_COMMERCIAL_CONCERNS.md`**.

## 1. How to produce real evidence

* On your own machine (best signal; home IPs are usually accepted by stats.nba.com):
  `python tools/probe_sources.py --acknowledge-terms --sources NBA_STATS NBA_LIVE NBA_INJURY_REPORT --out probe_report.json`
  or use the **Sources** panel (enable, then Probe). Results land in `source_probe` and feed the capability matrix.
* GitHub Actions `provider-probe` job (Windows cloud runner). nba_api's README warns stats.nba.com ignores many cloud IP ranges, so a timeout there is expected and is **not** evidence the endpoints are down.

## 1b. First real probe results (GitHub Actions windows-latest, 2026-10-25, run 40250448191, commit 4c6f80c)

Cloud runner, no keys, 3 of 13 probes ok:

| Source / capability | Result |
|---|---|
| `NBA_LIVE` live_scoreboard | **ok**, 7 games |
| `NBA_LIVE` live_box_score | **ok**, 29 rows |
| `NBA_LIVE` starting_lineups | **ok**, 10 rows (starter flags from the live box) |
| `NBA_STATS` (schedule, players, game logs, advanced, team logs, lineups, shot locations) | **timeout after 30 s**, all 9; box_score skipped because the schedule probe gave no game id |

Reading: cdn.nba.com live data is reachable from a cloud IP. stats.nba.com did not answer from this runner, which matches nba_api's warning about cloud IP ranges. This is **not** evidence that the stats endpoints are broken; it is evidence that they cannot be relied on from cloud hosts. The same probe must be run from your own machine before the stats side is judged. (Only row counts were recorded; sample payloads were not saved, so field-level parsing against real live data is still unverified.)

## 2. Priority 1: NBA.com/stats via nba_api

nba_api is MIT-licensed, v1.12.0. The application does not depend on the library at runtime; it uses the library's catalogue (endpoint names, parameters, result-set headers) and its own rate-limited HTTP transport with browser-style headers.

| Your priority | Endpoint used (adapter capability) | Notes |
|---|---|---|
| Player game-by-game incl. historical minutes | `playergamelogs` (`player_game_logs`) | One request returns all players' games for a season, with `MIN`. Loop seasons for history |
| Advanced per-game (usage, pace, ratings) | `playergamelogs` MeasureType=Advanced (`player_game_logs_advanced`) | |
| Team game logs / scores | `teamgamelogs` | |
| Player + team season stats, advanced | `leaguedashplayerstats`, `leaguedashteamstats` (Base/Advanced) | Usage%, TS%, PIE, pace, ORtg/DRtg |
| Lineup combinations | `leaguedashlineups` GroupQuantity 2-5 | |
| Shooting locations | `leaguedashplayershotlocations` (zones), `shotchartdetail` (shot-level x/y) | Zones have a two-level header, handled |
| Box scores with starters | `boxscoretraditionalv3` | Use V3 only |
| Play-by-play | `playbyplayv3` | |
| Schedule / game ids | `scheduleleaguev2` | |
| Live scoreboard, live box (starter flag, `notPlayingReason`) | cdn.nba.com live endpoints (`NBA_LIVE`) | Game-day inactives and starters; not historical |

**Known upstream failures (from the library's own catalogue):** 5 endpoints return HTTP 500 and 11 were removed, including V2 box scores, `playbyplay`/`playbyplayv2`, `scoreboardv2`, `leagueleaders`. They are listed in `KNOWN_BROKEN` and a test asserts none is used. Whether any have since been fixed is unverified.

**Strengths:** deepest free coverage by far, history back decades, tracking-style and lineup data nothing else offers at this price (zero).
**Weaknesses:** unofficial and undocumented; may change or block without notice; cloud IPs frequently ignored; no injury or projected-starter data; ToU question (see licensing doc).

## 3. Gap fillers

| Gap | Source | Evidence | Notes |
|---|---|---|---|
| Injuries (primary) | **Official NBA injury report PDF** (`NBA_INJURY_REPORT`, new) | documented (public search results show file names `Injury-Report_YYYY-MM-DD_HH_MMAM/PM.pdf`; one 2026-04-27 sample showed columns Game Date, Game Time (ET), Matchup, Team, Player Name, Current Status, Reason) | Free, exact publication time encoded in the file name, several releases per day on a quarter-hour grid. Statuses Available / Questionable / Probable / Out. Parser covers one observed layout, **PDF text layout unverified**, needs the optional `pypdf` package, ET-to-UTC done without tz database |
| Injuries (convenient) | BALLDONTLIE `player_injuries` | documented, ALL-STAR tier ($9.99/mo per vendor page) | JSON, easy; free tier is 5 req/min and excludes it |
| Injuries + lineups (vendor) | SportsDataIO `InjuredPlayers`, projected/confirmed lineups | documented, price by quote | Timeliest per vendor claims; not purchased |
| Confirmed starters (game day) | NBA_LIVE box score `starter` flag | library_catalog | Only after the lineup is set |
| Confirmed starters (historical) | `boxscoretraditionalv3` | library_catalog | Used for backfill; per-game backfill job not yet written |
| Odds | BALLDONTLIE GOAT ($39.99/mo) | documented | Optional |

BALLDONTLIE and SportsDataIO are implemented as adapters but **disabled by default**; no key was supplied and nothing was purchased.

## 4. Not evaluated

Sportradar, MySportsFeeds, API-NBA (RapidAPI), ESPN's unofficial endpoints, Basketball-Reference, and public-reporter feeds. No terms read, no mapping written.

## 5. Interchangeable adapters

`SourceRouter` routes by capability through an editable priority list with fallback; `fetch_all` combines sources; each result keeps provenance in `source_log`. Default order: injuries `NBA_INJURY_REPORT > SPORTSDATAIO > BALLDONTLIE > NBA_LIVE`; stats `NBA_STATS` first. Adding a provider means one `DataSource` subclass and a priority entry; the model, projector and UI are untouched. Real imports go to a separate `nba_real.sqlite` labelled `NBA_STATS_IMPORT`, never mixed with synthetic data; unavailable tracking values are NaN, not zero.

## 6. Still open

Live reliability and rate limits; whether the injury PDF parses on real files; `id_map` use for cross-vendor player ids; confirmed starters as a minutes feature (stored as events, not yet used by the model); scheduled refresh.
