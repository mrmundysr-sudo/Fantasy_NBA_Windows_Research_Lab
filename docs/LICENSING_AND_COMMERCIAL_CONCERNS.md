# Access, licensing and future commercial-release concerns

Kept separate from the evaluation as requested. Nothing here is legal advice, and **no provider is marked approved or prohibited**: the items below are what the page text said when fetched on 2026-10-25, plus open questions for the owner.

## NBA.com / stats.nba.com / cdn.nba.com (and therefore nba_api)

* **Found in the NBA.com Terms of Use page text (Section 9, fetched 2026-10-25; verify against the live page yourself):** statistics may not be used for fantasy games, commercial products, or databases of comprehensive, regularly updated statistics without consent; permitted use is described as news reporting or private, non-commercial use.
* **Why it matters here:** this project is a private research lab that stores statistics in a local database and produces fantasy projections. Whether private, single-owner research is inside the permitted use, and whether a fantasy-oriented database is inside the restriction, is an interpretation only the owner or counsel can make. **Status: unresolved, not assumed either way.**
* nba_api is MIT-licensed, but its licence covers the code, not the data it reaches. The library states it is unofficial and not affiliated with the NBA.
* Access: undocumented endpoints, no SLA, cloud IP ranges often ignored, may change or block without notice. The adapter rate-limits itself and identifies as a browser-style client; the owner should decide whether that is acceptable.
* **Release action if needed:** keep NBA-sourced data private and local; for any distribution, obtain a licence from NBA/league data partners or substitute a licensed vendor; remove stored NBA statistics from any shipped build.
* Gate: the source is disabled by default and requires the owner to type the acknowledgement sentence naming the terms URL; the acknowledgement is stored.

## Official NBA injury report (`official.nba.com`, `ak-static.cms.nba.com`)

Terms not separately reviewed; the NBA.com Terms of Use may govern it. Whether an injury report counts as a restricted statistic is not something this app decides. Status: unreviewed.

## BALLDONTLIE

Terms (`https://www.balldontlie.io/terms`) **not read**. Pricing per vendor page: Free $0 / 5 req/min; ALL-STAR $9.99 / 60; GOAT $39.99 / 600 (per sport). Injuries need ALL-STAR; lineups, advanced, box scores, odds need GOAT. No key held, nothing purchased. Redistribution and fantasy-use rights unknown.

## SportsDataIO

Vendor FAQ (fetched 2026-10-25): commercial licences allow storing responses and using them in models/analytics; reselling the feed as a data product is not permitted; attribution required for commercial licences; personal "Discovery Lab" plans are non-commercial only. Price by quote. Your actual rights depend on the contract you would sign. Nothing purchased.

## Other

Sportradar, MySportsFeeds, API-NBA, ESPN unofficial endpoints, Basketball-Reference: not reviewed.

## Decisions needed from the owner

1. Is private research use of NBA.com statistics acceptable under your reading of Section 9?
2. If a public release is ever planned, which licensed source replaces NBA-sourced data?
3. Approval (or not) to buy any keyed feed. None will be purchased without it.
