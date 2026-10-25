# Touch Fantasy NBA — Private Windows Research Lab
## Mission
Build an ambitious, online-first, Windows-only NBA statistical research and fantasy prediction workstation for private personal experimentation. Prioritize real, working analytical depth over mobile compatibility, templates, or cosmetic polish. This is not yet a public fantasy league or Android store product.

## Core principle
Minutes determine opportunity; player skill and style, teammates, coaching, offensive and defensive schemes, opponent, pace, injuries, and game context shape the statistical outcome. Predict minutes distributions first, then conditional statistical distributions. Never reduce the system to fantasy points per minute.

## Primary experiences
1. **Fantasy Picks:** selectable rankings for overall expected production, upside, consistency, minutes opportunity, matchup, defensive specialists, injury opportunities, and value when cost data exists; scoring-system selector; search for players and teams; explanations, uncertainty, freshness, and projected minutes.
2. **Advanced Analytics Workbench:** searchable registry of every supported metric; any number of checked metrics; player/team/coach/lineup/season selection; custom time windows; raw, per-game, per-36, and per-100-possession transforms where mathematically valid; filters, rankings, comparisons, correlations, custom formulas and weights; saved workspaces; inspectable games and charts. No arbitrary metric-selection cap; use sensible compute safeguards.
3. **Natural-Language Research:** English questions translated to a safe structured query representation and executed on actual records. Query Inspector exposes interpretation, formulas, filters, joins, sources, sample size, uncertainty, and supporting game rows. Editable query and rerun. Never invent answers or execute arbitrary AI-generated SQL.
4. **Windows Command Center:** multiple resizable panels, live information monitor, model tournament, prediction lab, data quality and audit dashboards, large tables and visualizations.
5. **Player and Team Explorer:** searchable individual/team profiles with historical and projected minutes, complete available statistics, lineup and coaching context, playing styles, scheme compatibility, injury impact, and fantasy ratings.

## Statistical coverage
- Traditional: minutes, games/starts, points, FG/2PT/3PT/FT made and attempted, percentages, offensive/defensive/total rebounds, assists, steals, blocks, turnovers, fouls, plus/minus.
- Advanced: TS%, eFG%, usage, offensive/defensive/net ratings, pace, assist/rebound/turnover percentages, on/off, per-36 and possession-based rates, validated impact metrics.
- Tracking where available: touches, time of possession, drives, potential/secondary assists, rebound chances, deflections, contested shots, screen assists, shot locations, transition, post touches, catch-and-shoot and pull-ups.
- Context: game date, season, opponent, venue, rest, travel, back-to-backs, overtime, injuries, minute limits, roster moves, starting lineups, teammates, substitutions, coaching eras, team pace, opponent schemes.
- Individual styles: on/off-ball scoring, isolation, drives, catch-and-shoot, pick-and-roll handling/finishing, passing, screens, rebounding, rim protection, perimeter defense, foul tendencies.
- Schemes: transition, motion, half-court, five-out, post, P&R/P&P, handoffs, switching, drop, blitz, zone, traps, pressure. Label inferred/unknown separately from directly observed.

Keep observed, derived, inferred, projected, simulated, and unavailable values distinct. Don't normalize percentages as counts. Record metric metadata, units, provenance, valid aggregation and normalization, formula versions, and dependencies. Avoid double-counting correlated predictors.

## Live online intelligence
Online-first; shared ingestion/prediction service rather than every client polling providers. Monitor injury reports, confirmed/projected lineups, restrictions, rotations, coaching, trades, schedule changes, and results. Timestamp publication and retrieval separately, compare sources, detect stale/conflicting data, and recompute affected projections when new facts arrive. Never claim real-time freshness without verification. Source/provider adapters are replaceable; investigate NBA official injury reporting, SportsDataIO, Sportradar and other legitimate sources. Verify access terms, coverage, rate limits, costs, and private research permissions before enabling integrations; do not buy services without approval.

## Modeling and auditing
Start with reproducible simple baselines; experiment with regression, ML, ensembles and simulations. Predict distributions for minutes, points, rebounds, assists, shooting, steals, blocks, turnovers and configurable fantasy scoring. Respect team-minute and box-score constraints, overtime, missing data, and injury uncertainty. Preserve pregame feature snapshots, predictions, model versions and source timestamps. Evaluate chronologically out of sample, avoid leakage, compare against baselines, measure MAE/RMSE and calibration, slice errors by role, team, coach, injury, and matchup. Train candidate models after verified results, run model tournaments, promote only measured improvements, and support rollback. Never silently rewrite historical predictions or fantasy scoring rules.

## Experimental and eventual public product
The Windows research lab is the only current implementation target. Android, multiplayer fantasy league mechanics, commercial deployment, and public release are deferred. Keep future reuse possible without sacrificing Windows capability. Private research does not waive applicable law, binding access restrictions, or security controls. Document public-release concerns separately rather than imposing hypothetical commercial-product constraints on permissible research.

## First working vertical slice
Launchable Windows UI; metric registry; player/team search; flexible comparison table and chart; natural-language-to-structured-query with inspector; supporting game drill-down; basic minutes and stat projection; fantasy rankings; injury/lineup event demonstration; reproducible audit; tests; verified Windows packaging. Use clearly marked synthetic fixtures where real permitted data is not available. Report what is real, demonstrated, incomplete, and blocked.
