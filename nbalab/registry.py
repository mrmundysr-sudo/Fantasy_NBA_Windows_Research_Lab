"""Metric registry: every supported metric with provenance and validity metadata.

Design rules (from PROJECT_VISION.md):
  * observed / derived / inferred / projected / simulated / unavailable stay distinct.
  * Ratios and percentages are computed from window SUMS of their components, never as
    an average of per-game ratios, and are never normalized like counts.
  * Each metric records unit, valid normalizations, dependencies and a formula version.
  * All percentage metrics are expressed in percentage points (0-100).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

from . import FORMULA_VERSION
from .safe_eval import names_in

KINDS = ("observed", "derived", "inferred", "projected", "simulated", "unavailable")
ALL_TRANSFORMS = ("raw", "per_game", "per36", "per100")
PLAYER = "player"
TEAM = "team"
COACH = "coach"
LINEUP = "lineup"
ALL_SCOPES = (PLAYER, TEAM, COACH, LINEUP)
TEAMLIKE = (TEAM, COACH, LINEUP)


@dataclass(frozen=True)
class Metric:
    id: str
    name: str
    category: str                      # traditional | advanced | tracking | context | style | scheme | projection
    kind: str                          # see KINDS
    unit: str                          # count | minutes | pct | rate | rating | ratio | score | flag
    scope: Tuple[str, ...]
    description: str
    expr: Optional[str] = None         # formula over window-sum columns; None for base column sums
    additive: bool = False             # True when the window value is a plain sum (count-like)
    transforms: Tuple[str, ...] = ("raw",)
    better: str = "higher"             # higher | lower | neutral
    aliases: Tuple[str, ...] = ()
    formula_version: str = FORMULA_VERSION
    source: str = "box score"
    status: str = "available"          # available | unavailable
    unavailable_reason: str = ""
    dependencies: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scope"] = list(self.scope)
        d["transforms"] = list(self.transforms)
        d["aliases"] = list(self.aliases)
        d["dependencies"] = list(self.dependencies)
        return d


_COUNT_T = ("raw", "per_game", "per36", "per100")
_REGISTRY: Dict[str, Metric] = {}
_ALIAS: Dict[str, str] = {}


def _add(m: Metric) -> None:
    if m.kind not in KINDS:
        raise ValueError(f"bad kind {m.kind}")
    if m.id in _REGISTRY:
        raise ValueError(f"duplicate metric {m.id}")
    if m.expr:
        deps = tuple(sorted(names_in(m.expr)))
        m = Metric(**{**asdict(m), "scope": m.scope, "transforms": m.transforms,
                      "aliases": m.aliases, "dependencies": deps})
    _REGISTRY[m.id] = m
    for a in (m.id, m.name.lower(), *[x.lower() for x in m.aliases]):
        _ALIAS.setdefault(a, m.id)


def _count(id, name, desc, aliases=(), cat="traditional", better="higher", scope=ALL_SCOPES,
           source="box score", kind="observed", unit="count", transforms=_COUNT_T):
    _add(Metric(id, name, cat, kind, unit, scope, desc, None, True, transforms, better, aliases, source=source))


# ---- Traditional counting stats (observed) -------------------------------------------
_add(Metric("gp", "Games Played", "traditional", "observed", "count", ALL_SCOPES,
            "Games with recorded minutes (player) or games played (team/coach/lineup).", None, True, ("raw",),
            aliases=("games played", "gp")))
_add(Metric("gs", "Games Started", "traditional", "observed", "count", (PLAYER,),
            "Games started.", None, True, ("raw",), aliases=("starts", "games started")))
_add(Metric("min", "Minutes", "traditional", "observed", "minutes", ALL_SCOPES,
            "Total minutes played.", None, True, ("raw", "per_game"), aliases=("minutes", "mpg", "mp")))
_count("pts", "Points", "Points scored.", ("points", "scoring", "ppg"))
_count("fgm", "Field Goals Made", "Field goals made.", ("fg made", "fgm"))
_count("fga", "Field Goals Attempted", "Field goals attempted.", ("fg attempted", "shots", "shot attempts"))
_count("fg3m", "3-Pointers Made", "Three-point field goals made.", ("3pm", "threes", "three pointers made", "3 pointers made", "3s made"))
_count("fg3a", "3-Pointers Attempted", "Three-point attempts.", ("3pa", "three point attempts", "3 point attempts", "3s attempted"))
_count("ftm", "Free Throws Made", "Free throws made.", ("ft made",))
_count("fta", "Free Throws Attempted", "Free throws attempted.", ("ft attempted", "free throw attempts"))
_add(Metric("fg2m", "2-Pointers Made", "traditional", "derived", "count", ALL_SCOPES, "Two-point makes = FGM - 3PM.",
            "fgm - fg3m", True, _COUNT_T, aliases=("2pm", "twos made")))
_add(Metric("fg2a", "2-Pointers Attempted", "traditional", "derived", "count", ALL_SCOPES, "Two-point attempts = FGA - 3PA.",
            "fga - fg3a", True, _COUNT_T, aliases=("2pa", "two point attempts")))
_count("oreb", "Offensive Rebounds", "Offensive rebounds.", ("offensive rebounds", "orb"))
_count("dreb", "Defensive Rebounds", "Defensive rebounds.", ("defensive rebounds", "drb"))
_count("reb", "Rebounds", "Total rebounds.", ("rebounds", "boards", "rpg", "trb"))
_count("ast", "Assists", "Assists.", ("assists", "dimes", "apg"))
_count("stl", "Steals", "Steals.", ("steals", "spg"))
_count("blk", "Blocks", "Blocks.", ("blocks", "bpg"))
_count("tov", "Turnovers", "Turnovers.", ("turnovers", "tpg"), better="lower")
_count("pf", "Personal Fouls", "Personal fouls.", ("fouls", "personal fouls"), better="lower")
_count("pm", "Plus/Minus", "Team point differential while on the floor (player) or point differential (team).",
       ("plus minus", "+/-", "plus/minus"), cat="traditional", scope=(PLAYER,))
_count("wins", "Wins", "Games won by the team while the entity was in the sample.", ("w",), cat="context")

# ---- Shooting efficiency (derived ratios; computed from sums) -------------------------
_add(Metric("fg_pct", "FG%", "traditional", "derived", "pct", ALL_SCOPES, "Field goal percentage.",
            "100 * fgm / fga", aliases=("fg pct", "field goal percentage", "fg%", "shooting percentage")))
_add(Metric("fg3_pct", "3P%", "traditional", "derived", "pct", ALL_SCOPES, "Three-point percentage.",
            "100 * fg3m / fg3a", aliases=("3p%", "three point percentage", "3 point percentage", "3pt%", "3pt pct", "three pct")))
_add(Metric("fg2_pct", "2P%", "traditional", "derived", "pct", ALL_SCOPES, "Two-point percentage.",
            "100 * (fgm - fg3m) / (fga - fg3a)", aliases=("2p%", "two point percentage", "2pt%")))
_add(Metric("ft_pct", "FT%", "traditional", "derived", "pct", ALL_SCOPES, "Free throw percentage.",
            "100 * ftm / fta", aliases=("ft pct", "free throw percentage", "ft%")))
_add(Metric("efg_pct", "eFG%", "advanced", "derived", "pct", ALL_SCOPES, "Effective FG% = (FGM + 0.5*3PM)/FGA.",
            "100 * (fgm + 0.5 * fg3m) / fga", aliases=("efg", "effective field goal percentage", "efg%")))
_add(Metric("ts_pct", "TS%", "advanced", "derived", "pct", ALL_SCOPES, "True shooting = PTS / (2*(FGA + 0.44*FTA)).",
            "100 * pts / (2 * (fga + 0.44 * fta))", aliases=("ts", "true shooting", "true shooting percentage", "ts%")))
_add(Metric("fta_rate", "FT Rate", "advanced", "derived", "ratio", ALL_SCOPES, "Free throw attempts per field goal attempt.",
            "fta / fga", aliases=("free throw rate", "fta per fga", "ftr")))
_add(Metric("three_rate", "3PA Rate", "advanced", "derived", "ratio", ALL_SCOPES, "Share of FGA that are threes.",
            "fg3a / fga", aliases=("three point rate", "3 point rate", "3par")))
_add(Metric("pts_per_shot", "Points per Shot", "advanced", "derived", "ratio", ALL_SCOPES, "Points per FGA.",
            "pts / fga", aliases=("pps", "points per fga")))

# ---- Player rates that need team context (derived) -----------------------------------
_PF = (PLAYER,)
_add(Metric("usg_pct", "Usage%", "advanced", "derived", "pct", _PF,
            "Share of team possessions used while on the floor: 100*((FGA+0.44*FTA+TOV)*(TmMIN/5))/(MIN*(TmFGA+0.44*TmFTA+TmTOV)).",
            "100 * ((fga + 0.44 * fta + tov) * (tm_min / 5)) / (min * (tm_fga + 0.44 * tm_fta + tm_tov))",
            aliases=("usage", "usage rate", "usg", "usg%", "usage percentage"), better="neutral"))
_add(Metric("ast_pct", "AST%", "advanced", "derived", "pct", _PF,
            "Assist percentage: assists as a share of teammate field goals while on floor.",
            "100 * ast / ((min / (tm_min / 5)) * tm_fgm - fgm)", aliases=("assist percentage", "assist rate", "ast%")))
_add(Metric("reb_pct", "REB%", "advanced", "derived", "pct", _PF,
            "Rebound percentage: share of available rebounds grabbed while on floor.",
            "100 * (reb * (tm_min / 5)) / (min * (tm_reb + opp_reb))", aliases=("rebound percentage", "rebound rate", "reb%")))
_add(Metric("oreb_pct", "OREB%", "advanced", "derived", "pct", _PF,
            "Offensive rebound percentage.", "100 * (oreb * (tm_min / 5)) / (min * (tm_oreb + opp_dreb))",
            aliases=("offensive rebound percentage", "oreb%")))
_add(Metric("dreb_pct", "DREB%", "advanced", "derived", "pct", _PF,
            "Defensive rebound percentage.", "100 * (dreb * (tm_min / 5)) / (min * (tm_dreb + opp_oreb))",
            aliases=("defensive rebound percentage", "dreb%")))
_add(Metric("tov_pct", "TOV%", "advanced", "derived", "pct", ALL_SCOPES,
            "Turnovers per 100 plays: 100*TOV/(FGA+0.44*FTA+TOV).", "100 * tov / (fga + 0.44 * fta + tov)",
            better="lower", aliases=("turnover percentage", "turnover rate", "tov%")))
_add(Metric("stl_pct", "STL%", "advanced", "derived", "pct", _PF,
            "Steal percentage: steals per 100 opponent possessions while on floor.",
            "100 * (stl * (tm_min / 5)) / (min * opp_poss)", aliases=("steal percentage", "steal rate", "stl%")))
_add(Metric("blk_pct", "BLK%", "advanced", "derived", "pct", _PF,
            "Block percentage: blocks per 100 opponent two-point attempts while on floor.",
            "100 * (blk * (tm_min / 5)) / (min * (opp_fga - opp_fg3a))", aliases=("block percentage", "block rate", "blk%")))
_add(Metric("mpg", "Minutes per Game", "traditional", "derived", "minutes", ALL_SCOPES, "Minutes per game played.",
            "min / gp", aliases=("minutes per game", "avg minutes")))
_add(Metric("min_share", "Team Minutes Share", "context", "derived", "pct", _PF,
            "Share of team minutes played (opportunity measure).", "100 * min / tm_min", aliases=("minutes share", "opportunity")))
_add(Metric("gmsc", "Game Score", "advanced", "derived", "score", _PF,
            "Hollinger Game Score summed over games (additive); use per-game for the average.",
            None, True, ("raw", "per_game", "per36", "per100"), aliases=("game score",)))
_add(Metric("pie", "PIE", "advanced", "derived", "pct", _PF,
            "Player Impact Estimate: player's share of total game events.", "100 * pie_num / pie_den",
            aliases=("player impact estimate",)))
_add(Metric("dd", "Double-Doubles", "context", "derived", "count", _PF, "Games with 10+ in at least two of PTS/REB/AST/STL/BLK.",
            None, True, ("raw", "per_game"), aliases=("double doubles", "double-doubles")))
_add(Metric("td", "Triple-Doubles", "context", "derived", "count", _PF, "Games with 10+ in at least three categories.",
            None, True, ("raw", "per_game"), aliases=("triple doubles", "triple-doubles")))
_add(Metric("ast_to", "AST/TO", "advanced", "derived", "ratio", ALL_SCOPES, "Assist to turnover ratio.", "ast / tov",
            aliases=("assist to turnover", "ast to", "ast/to")))
_add(Metric("pm_per36", "Plus/Minus per 36", "advanced", "derived", "rate", _PF, "Plus/minus per 36 minutes (noisy; context dependent).",
            "36 * pm / min", better="higher", aliases=("pm36",)))

# ---- Team / coach / lineup ratings ---------------------------------------------------
_T = TEAMLIKE
_add(Metric("poss", "Possessions", "advanced", "derived", "count", _T, "Estimated team possessions.", None, True, ("raw", "per_game"),
            aliases=("possessions",), source="box score estimate"))
_add(Metric("ortg", "Offensive Rating", "advanced", "derived", "rating", _T, "Points scored per 100 possessions.",
            "100 * pts / tm_poss", aliases=("offensive rating", "ortg", "off rating")))
_add(Metric("drtg", "Defensive Rating", "advanced", "derived", "rating", _T, "Points allowed per 100 possessions.",
            "100 * opp_pts / opp_poss", better="lower", aliases=("defensive rating", "drtg", "def rating")))
_add(Metric("nrtg", "Net Rating", "advanced", "derived", "rating", _T, "ORtg minus DRtg.",
            "100 * pts / tm_poss - 100 * opp_pts / opp_poss", aliases=("net rating", "nrtg")))
_add(Metric("pace", "Pace", "advanced", "derived", "rate", _T, "Possessions per 48 minutes.",
            "48 * (tm_poss + opp_poss) / (2 * (min / 5))", better="neutral", aliases=("pace",)))
_add(Metric("opp_pts_pg", "Opponent PPG", "advanced", "derived", "score", _T, "Opponent points per game.",
            "opp_pts / gp", better="lower", aliases=("points allowed", "opp ppg")))
_add(Metric("win_pct", "Win%", "context", "derived", "pct", _T, "Winning percentage.", "100 * wins / gp",
            aliases=("winning percentage", "win percentage", "win%")))

# ---- Tracking (the synthetic fixture generates a subset; real feeds may add more) -----
_count("touches", "Touches", "Times the player touched the ball (tracking).", (), cat="tracking", source="tracking", scope=(PLAYER,))
_count("drives", "Drives", "Drives to the basket (tracking).", (), cat="tracking", source="tracking", scope=(PLAYER,))
_count("pot_ast", "Potential Assists", "Passes that would be assists if the shot were made (tracking).",
       ("potential assists",), cat="tracking", source="tracking", scope=(PLAYER,))
_count("contested", "Contested Shots", "Opponent shots contested (tracking).", ("contested shots",), cat="tracking",
       source="tracking", scope=(PLAYER,))
_add(Metric("touch_usage", "Points per Touch", "tracking", "derived", "ratio", _PF, "Points per touch.", "pts / touches", aliases=("pts per touch",),
            source="tracking"))
for _id, _nm, _why in [
    ("time_of_poss", "Time of Possession", "Not in the synthetic fixture; needs a tracking provider."),
    ("deflections", "Deflections", "Not in the synthetic fixture; needs a hustle-stats provider."),
    ("screen_ast", "Screen Assists", "Not in the synthetic fixture; needs a hustle-stats provider."),
    ("shot_loc", "Shot Locations", "Not in the synthetic fixture; needs shot-chart data."),
    ("onoff", "On/Off Net Rating", "Needs lineup/play-by-play data; fixture has none."),
    ("epm_like", "Validated Impact Metric", "No validated public impact model has been integrated yet."),
    ("catch_shoot", "Catch-and-Shoot Stats", "Needs tracking provider."),
    ("pnr_handler", "Pick-and-Roll Handler Stats", "Needs play-type data."),
]:
    _add(Metric(_id, _nm, "tracking" if _id not in ("onoff", "epm_like") else "advanced", "unavailable", "count", ALL_SCOPES, _nm,
                status="unavailable", unavailable_reason=_why, source="none"))

# ---- Style / scheme labels: inferred (never presented as observed) -------------------
_add(Metric("style_3pt_heavy", "Three-Point Heavy (inferred)", "style", "inferred", "pct", ALL_SCOPES,
            "Inferred style label: 3PA rate, shown as percent of FGA. Inferred from box-score shot mix, not observed play types.",
            "100 * fg3a / fga", aliases=("three-point heavy",), better="neutral", source="inferred from box score"))
_add(Metric("style_playmaking", "Playmaking Load (inferred)", "style", "inferred", "ratio", _PF,
            "Inferred: assists per usage-adjusted possession (AST per play used).",
            "ast / (fga + 0.44 * fta + tov)", better="neutral", source="inferred from box score"))
_add(Metric("scheme_pace_tier", "Fast-Pace Scheme (inferred)", "scheme", "inferred", "rate", _T,
            "Inferred scheme indicator: team pace. Scheme labels beyond pace require play-type data and are unavailable.",
            "48 * (tm_poss + opp_poss) / (2 * (min / 5))", better="neutral", source="inferred from box score"))

# ---- Projected metrics (from the model prediction store, not from box scores) --------
for _id, _nm, _u in [("proj_min", "Projected Minutes (next game)", "minutes"),
                     ("proj_pts", "Projected Points (next game)", "count"),
                     ("proj_reb", "Projected Rebounds (next game)", "count"),
                     ("proj_ast", "Projected Assists (next game)", "count"),
                     ("proj_fpts", "Projected Fantasy Points (next game)", "score"),
                     ("proj_fpts_p90", "Fantasy Upside p90 (next game)", "score")]:
    _add(Metric(_id, _nm, "projection", "projected", _u, (PLAYER,), "Model projection for the next scheduled game. SYNTHETIC unless fed real data.",
                None, False, ("raw",), source="model prediction store"))


def all_metrics() -> List[Metric]:
    return list(_REGISTRY.values())


def get(metric_id: str) -> Metric:
    m = _REGISTRY.get(metric_id)
    if m is None:
        raise KeyError(metric_id)
    return m


def resolve(token: str) -> Optional[str]:
    return _ALIAS.get(token.strip().lower())


def search(q: str, limit: int = 50) -> List[Metric]:
    q = q.strip().lower()
    if not q:
        return all_metrics()[:limit]
    scored = []
    for m in _REGISTRY.values():
        hay = " ".join([m.id, m.name, m.category, m.kind, m.description, *m.aliases]).lower()
        if q in hay:
            score = 0 if q in (m.id, m.name.lower()) else (1 if q in m.name.lower() else 2)
            scored.append((score, m.name, m))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [m for _, _, m in scored[:limit]]


def check_dependencies() -> List[str]:
    """Registry self-check used by tests: every formula only references known columns."""
    from .schema import SUM_COLUMNS
    problems = []
    for m in _REGISTRY.values():
        for d in m.dependencies:
            if d not in SUM_COLUMNS and d not in _REGISTRY:
                problems.append(f"{m.id} depends on unknown column {d}")
    return problems
