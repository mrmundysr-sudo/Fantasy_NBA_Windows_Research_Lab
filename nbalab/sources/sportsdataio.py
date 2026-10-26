"""SportsDataIO NBA (api.sportsdata.io/v3/nba). Key from env SPORTSDATAIO_API_KEY (header Ocp-Apim-Subscription-Key).

Paths are from the vendor's NBA documentation (fetched 2026-10-25). The free trial covers only UEFA Champions League per that page,
so NBA data needs a quoted commercial/personal licence: nothing is bought here. Field names for injuries/lineups were not shown on
the docs page (they point to a data dictionary), so normalizers read the commonly documented Player fields tolerantly and the
evidence level is 'documented' (path) / 'unverified' (shape) until a keyed probe succeeds.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from .base import CapabilityInfo, DataSource, Fetched, SourceError
from .nba_stats import parse_minutes

BASE = "https://api.sportsdata.io/v3/nba"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
STATUS_MAP = {"out": "out", "out for season": "out", "doubtful": "doubtful", "questionable": "questionable", "probable": "probable", "day-to-day": "questionable", "scrambled": "questionable"}

# capability -> (path template, polling interval doc, evidence, notes)
PATHS = {
    "injuries": ("/projections/json/InjuredPlayers", "1 min", "documented", "Player Details - by Injured"),
    "starting_lineups": ("/projections/json/StartingLineupsByDate/{date}", "3 min", "documented", "Projected and confirmed lineups"),
    "player_game_logs": ("/stats/json/PlayerGameStatsByDateFinal/{date}", "", "documented", "Final player game stats for a date"),
    "schedule": ("/scores/json/Games/{season}", "5 min", "documented", "Season schedule (season like 2027, optional PRE/POST suffix)"),
    "player_season_stats": ("/stats/json/PlayerSeasonStats/{season}", "", "documented", "Season stats"),
    "team_season_advanced": ("/scores/json/TeamSeasonStats/{season}", "", "documented", "Team season stats; advanced fields not confirmed"),
    "box_score": ("/stats/json/BoxScoresFinal/{date}", "", "documented", "Final box scores by date"),
    "play_by_play": ("/pbp/json/PlayByPlayFinal/{game_id}", "", "documented", "Final play-by-play"),
}


def sdio_date(iso: str) -> str:
    y, m, d = iso[:10].split("-")
    return f"{y}-{MONTHS[int(m) - 1]}-{int(d):02d}"


class SportsDataIOSource(DataSource):
    name = "SPORTSDATAIO"
    kind = "multi"
    homepage = "https://sportsdata.io"
    terms_url = "https://sportsdata.io/help/data-rights-and-licensing-questions"
    terms_status = "permissive_noted"
    terms_summary = ("Vendor FAQ (fetched 2026-10-25): commercial licences allow storing responses and use in models/analytics; reselling the feed as a data product is "
                     "not permitted; attribution required for commercial licences; 'Discovery Lab' personal plans are non-commercial only. Pricing is by quote. "
                     "Your actual rights depend on the agreement you sign, which this app has not seen.")
    requires_key = True
    key_env = "SPORTSDATAIO_API_KEY"
    min_interval_s = 1.0

    def capabilities(self) -> Dict[str, CapabilityInfo]:
        return {cap: CapabilityInfo(cap, p, "quote", "documented" if cap not in ("injuries", "starting_lineups") else "documented", note + (f" (poll every {iv})" if iv else "")) for cap, (p, iv, _e, note) in PATHS.items()}

    def _fetch(self, cap: str, **params) -> Fetched:
        path, _iv, _ev, _n = PATHS[cap]
        fmt = dict(params)
        if "date" in fmt:
            fmt["date"] = sdio_date(str(fmt["date"]))
        try:
            url = BASE + path.format(**fmt)
        except KeyError as e:
            raise SourceError("unsupported", f"{cap} needs parameter {e.args[0]}")
        res = self.transport.get(url, None, headers={"Ocp-Apim-Subscription-Key": self.key() or ""}, timeout=30.0)
        obj = res.json()
        rows = obj if isinstance(obj, list) else [obj]
        recs = self.normalize_all(cap, rows)
        meta = {"url": url, "status": res.status, "elapsed_ms": round(res.elapsed_ms), "rows": len(recs), "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        return Fetched(cap, self.name, recs, meta, sample=recs[:2])

    def normalize_all(self, cap: str, rows: List[dict]) -> List[dict]:
        s = self.name
        if cap == "injuries":
            out = []
            for r in rows:
                st = str(r.get("InjuryStatus") or "").strip()
                if not st:
                    continue
                out.append({"source": s, "player_id": r.get("PlayerID"), "player_name": f"{r.get('FirstName','')} {r.get('LastName','')}".strip() or r.get("Name"), "team_abbr": r.get("Team"),
                            "status_raw": st, "status": STATUS_MAP.get(st.lower(), st.lower()), "detail": " ".join(x for x in (r.get("InjuryBodyPart"), r.get("InjuryNotes")) if x),
                            "updated_at": r.get("Updated"), "start_date": r.get("InjuryStartDate")})
            return out
        if cap == "starting_lineups":
            out = []
            for g in rows:
                for side, abbr_key in (("HomeLineup", "HomeTeam"), ("AwayLineup", "AwayTeam")):
                    lineup = g.get(side) or g.get(side.replace("Lineup", "Starters")) or []
                    for p in lineup if isinstance(lineup, list) else []:
                        out.append({"source": s, "game_id": g.get("GameID"), "team_abbr": g.get(abbr_key), "player_id": p.get("PlayerID"), "player_name": p.get("Name") or f"{p.get('FirstName','')} {p.get('LastName','')}".strip(),
                                    "position": p.get("Position"), "starter": True, "confirmed": g.get("Confirmed", p.get("Confirmed")), "updated_at": g.get("Updated")})
            return out
        if cap == "player_game_logs":
            return [{"source": s, "game_id": str(r.get("GameID")), "game_date": str(r.get("Day") or r.get("DateTime") or "")[:10], "season": r.get("Season"), "player_id": r.get("PlayerID"),
                     "player_name": r.get("Name"), "team_abbr": r.get("Team"), "min": r.get("Minutes"), "pts": r.get("Points"), "fgm": r.get("FieldGoalsMade"), "fga": r.get("FieldGoalsAttempted"),
                     "fg3m": r.get("ThreePointersMade"), "fg3a": r.get("ThreePointersAttempted"), "ftm": r.get("FreeThrowsMade"), "fta": r.get("FreeThrowsAttempted"),
                     "oreb": r.get("OffensiveRebounds"), "dreb": r.get("DefensiveRebounds"), "reb": r.get("Rebounds"), "ast": r.get("Assists"), "stl": r.get("Steals"), "blk": r.get("BlockedShots"),
                     "tov": r.get("Turnovers"), "pf": r.get("PersonalFouls"), "pm": r.get("PlusMinus"), "started": bool(r.get("Started")) if r.get("Started") is not None else None,
                     "usage_pct": r.get("UsageRatePercentage")} for r in rows]
        return [dict(r, source=s) for r in rows]
