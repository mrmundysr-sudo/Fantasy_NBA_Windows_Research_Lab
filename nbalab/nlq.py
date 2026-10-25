"""Natural-language research: English -> structured query -> deterministic engine.

Hard rules:
  * Text is only ever translated into the structured query defined in engine.py. No SQL text is
    generated or executed, and no statistic is produced by language-model free text.
  * Anything not understood is reported (``unresolved``); ambiguous names are never guessed.
  * Every answer carries a Query Inspector payload: interpretation, formulas and versions, filters,
    logical joins, sources, sample size, uncertainty method, and supporting game rows. The structured
    query is returned so it can be edited and re-run.
  * Headline sentences are templated from computed numbers, never invented.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from . import registry
from .engine import Engine, QueryError, MetricError

NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30}
SHORTHAND = {"ppg": ("pts", "per_game"), "apg": ("ast", "per_game"), "rpg": ("reb", "per_game"), "spg": ("stl", "per_game"),
             "bpg": ("blk", "per_game"), "mpg": ("mpg", "raw"), "tpg": ("tov", "per_game")}
DESC_WORDS = {"most", "highest", "best", "top", "leaders", "leader", "leading", "greatest", "largest", "biggest", "more"}
ASC_WORDS = {"lowest", "fewest", "worst", "least", "bottom", "smallest"}
POSITION_WORDS = {"point guards": "PG", "point guard": "PG", "shooting guards": "SG", "shooting guard": "SG", "small forwards": "SF",
                  "small forward": "SF", "power forwards": "PF", "power forward": "PF", "centers": "C", "center": "C"}
EXAMPLES = [
    "Top 10 players by points per 36 minutes this season with at least 300 minutes",
    "Compare <player A> and <player B> on usage, TS% and assists per game",
    "Which team has the best net rating?",
    "<player> points per game in the last 10 games",
    "Correlation between usage and assists per 36",
    "<player> rebounds per game on back-to-backs",
    "Top 5 players by assists when <teammate> is out",
]


def _norm(text: str) -> str:
    t = text.lower().replace("’", "'").replace("‑", "-")
    t = re.sub(r"[^a-z0-9%+/\-' .]", " ", t)
    t = re.sub(r"(?<![0-9])\.|\.(?![0-9])", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class NLQ:
    def __init__(self, engine: Engine):
        self.engine = engine
        ds = engine.ds
        self.ds = ds
        self.player_full: Dict[str, List[int]] = {}
        self.player_last: Dict[str, List[int]] = {}
        for pid, p in ds.players.items():
            n = p["name"].lower()
            self.player_full.setdefault(n, []).append(pid)
            self.player_last.setdefault(n.split(" ")[-1], []).append(pid)
        self.team_names: Dict[str, List[int]] = {}
        for tid, t in ds.teams.items():
            for key in (t["name"].lower(), t["city"].lower(), t["name"].lower().split(" ")[-1], t["abbr"].lower()):
                self.team_names.setdefault(key, []).append(tid)
        self.coach_names: Dict[str, List[int]] = {}
        for cid, c in ds.coaches.items():
            self.coach_names.setdefault(c["name"].lower(), []).append(cid)
        self.metric_alias: List[Tuple[str, str]] = []
        for m in registry.all_metrics():
            for a in {m.id, m.name.lower(), *[x.lower() for x in m.aliases]}:
                if len(a) >= 2 and a not in SHORTHAND:
                    self.metric_alias.append((a, m.id))
        self.metric_alias.sort(key=lambda t: -len(t[0]))

    # ---------------------------------------------------------------------------------------------
    def _find(self, text: str, table: Dict[str, List[int]], used: List[Tuple[int, int]], kind: str):
        found = []
        for phrase in sorted(table, key=lambda s: -len(s)):
            for m in re.finditer(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text):
                s, e = m.span()
                if any(not (e <= us or s >= ue) for us, ue in used):
                    continue
                used.append((s, e))
                found.append({"token": phrase, "ids": table[phrase], "kind": kind, "span": (s, e)})
        return found

    # ---------------------------------------------------------------------------------------------
    def parse(self, text: str) -> dict:
        raw = text
        t = _norm(text)
        if not t:
            return self._fail(raw, "Empty question.", [])
        ds = self.ds
        used: List[Tuple[int, int]] = []
        notes: List[str] = []
        ambiguities: List[dict] = []

        # --- entities (longest phrases first so full names beat last names) ---
        ent = []
        ent += self._find(t, self.player_full, used, "player")
        ent += self._find(t, self.coach_names, used, "coach")
        ent += self._find(t, self.team_names, used, "team")
        ent += self._find(t, {k: v for k, v in self.player_last.items() if k not in self.player_full}, used, "player")
        players, teams_mentioned, coaches = [], [], []
        for e in sorted(ent, key=lambda x: x["span"][0]):
            if len(e["ids"]) > 1:
                ambiguities.append({"token": e["token"], "kind": e["kind"], "candidates": [self._cand(e["kind"], i) for i in e["ids"]]})
                continue
            eid = e["ids"][0]
            e["id"] = eid
            e["name"] = self._cand(e["kind"], eid)["name"]
            {"player": players, "team": teams_mentioned, "coach": coaches}[e["kind"]].append(e)
        if ambiguities:
            lines = [f"'{a['token']}' matches several {a['kind']}s: " + ", ".join(c["name"] for c in a["candidates"][:6]) for a in ambiguities]
            return self._fail(raw, "Ambiguous name(s) - please use the full name. " + " | ".join(lines), [], ambiguities=ambiguities)

        # blank entity spans so metric matching never reads inside names
        masked = list(t)
        for s, e in used:
            masked[s:e] = " " * (e - s)
        mt = "".join(masked)

        q: dict = {"entity": "player", "metrics": [], "window": {}, "filters": {}, "limit": 25, "sort": {}}
        filters_h: List[str] = []

        # --- seasons / windows ---
        seasons_found = [s for s in ds.seasons if re.search(r"(?<![a-z0-9])" + re.escape(s.lower()) + r"(?![a-z0-9])", mt)]
        if seasons_found:
            q["window"]["seasons"] = seasons_found
        elif re.search(r"\b(this|current) season\b", mt):
            q["window"]["seasons"] = [ds.seasons[-1]]
        elif re.search(r"\b(last|previous) season\b", mt) and len(ds.seasons) > 1:
            q["window"]["seasons"] = [ds.seasons[-2]]
        m = re.search(r"\b(?:last|past|previous)\s+(\d+|" + "|".join(NUM_WORDS) + r")\s+games?\b", mt)
        if m:
            q["window"]["last_n"] = self._num(m.group(1))
        m = re.search(r"\b(?:last|past)\s+(\d+)\s+days?\b", mt)
        if m:
            last = max(g["game_date"] for g in ds.games.values() if g["status"] == "final")
            from datetime import date, timedelta
            d0 = date.fromisoformat(last) - timedelta(days=int(m.group(1)))
            q["window"]["date_from"] = d0.isoformat()
            notes.append(f"'last {m.group(1)} days' is measured back from the latest game in the dataset ({last}), not today's date.")
        m = re.search(r"\bsince\s+(\d{4}-\d{2}-\d{2})\b", mt)
        if m:
            q["window"]["date_from"] = m.group(1)
        m = re.search(r"\bbetween\s+(\d{4}-\d{2}-\d{2})\s+and\s+(\d{4}-\d{2}-\d{2})\b", mt)
        if m:
            q["window"]["date_from"], q["window"]["date_to"] = m.group(1), m.group(2)

        # --- filters ---
        f = q["filters"]
        if re.search(r"\b(at home|home games?|in home games)\b", mt):
            f["home"] = True; filters_h.append("home games only")
        elif re.search(r"\b(on the road|away games?|road games?|away from home|on road)\b", mt):
            f["home"] = False; filters_h.append("away games only")
        if re.search(r"\bback[- ]to[- ]backs?\b|\bb2bs?\b", mt):
            f["b2b"] = True; filters_h.append("second night of a back-to-back (0 days rest)")
        m = re.search(r"\b(\d+)\+?\s*(?:or more )?days? (?:of )?rest\b", mt)
        if m:
            f["rest_min"] = int(m.group(1)); filters_h.append(f"at least {m.group(1)} days rest")
        if re.search(r"\b(in wins|wins only|in victories|when (?:they|the team) win)\b", mt):
            f["result"] = "W"; filters_h.append("team wins only")
        elif re.search(r"\b(in losses|losses only|in defeats|when (?:they|the team) lose)\b", mt):
            f["result"] = "L"; filters_h.append("team losses only")
        if re.search(r"\b(as a starter|when starting|starting games|in starts)\b", mt):
            f["started"] = True; filters_h.append("games started")
        elif re.search(r"\b(off the bench|as a reserve|coming off the bench)\b", mt):
            f["started"] = False; filters_h.append("games off the bench")
        if re.search(r"\b(in overtime|overtime games?|ot games?)\b", mt):
            f["ot"] = True; filters_h.append("overtime games only")
        m = re.search(r"\b(?:at least|min(?:imum)?|over|more than|\+)\s*(\d+)\s*(?:\+)?\s*minutes?\s*(?:per game|a game|in a game|each game)\b", mt)
        if m:
            f["min_minutes_game"] = float(m.group(1)); filters_h.append(f"games with {m.group(1)}+ minutes")
        elif re.search(r"\b(\d+)\+\s*minutes? (?:games?|per game)", mt):
            mm = re.search(r"\b(\d+)\+\s*minutes? (?:games?|per game)", mt)
            f["min_minutes_game"] = float(mm.group(1)); filters_h.append(f"games with {mm.group(1)}+ minutes")
        m = re.search(r"\bat least (\d+) (?:total )?minutes\b(?! per game)", mt)
        if m and "min_minutes_game" not in f:
            q["min_minutes"] = float(m.group(1)); filters_h.append(f"entities with at least {m.group(1)} total minutes")
        m = re.search(r"\bat least (\d+) games?\b", mt)
        if m:
            q["min_games"] = int(m.group(1)); filters_h.append(f"entities with at least {m.group(1)} games")

        # opponent / teammates relative to entity positions in the text
        subj_players, tm_out, tm_in = [], [], []
        for p in players:
            s, _e = p["span"]
            before = t[max(0, s - 40):s]
            after = t[p["span"][1]: p["span"][1] + 40]
            if re.search(r"\b(without|sans)\s*$", before) or re.match(r"\s*(is|are|was|were)?\s*(out|sitting|sits|sit|injured|missing|absent|resting|not playing|doesn't play|does not play|inactive)\b", after) or re.search(r"\bwhen\s*$", before) and re.match(r"\s*(is |are )?(out|injured|missing|sits|sitting|absent)", after):
                tm_out.append(p)
            elif re.search(r"\b(?:with|alongside|next to)\s*$", before) and subj_players_exist(players, p):
                tm_in.append(p)
            elif re.match(r"\s*(is|are)?\s*(playing|plays|healthy|available|active)\b", after) and re.search(r"\bwhen\s*$", before):
                tm_in.append(p)
            else:
                subj_players.append(p)
        if tm_out:
            f["teammates_out"] = [p["id"] for p in tm_out]
            filters_h.append("teammate(s) out: " + ", ".join(p["name"] for p in tm_out))
        if tm_in:
            f["teammates_in"] = [p["id"] for p in tm_in]
            filters_h.append("teammate(s) playing: " + ", ".join(p["name"] for p in tm_in))
        opp_ids, subj_teams = [], []
        for tm in teams_mentioned:
            before = t[max(0, tm["span"][0] - 25):tm["span"][0]]
            if re.search(r"\b(against|vs\.?|versus|facing|opposing)\s*(the\s*)?$", before):
                opp_ids.append(tm["id"])
            else:
                subj_teams.append(tm)
        if opp_ids:
            f["opponents"] = opp_ids
            filters_h.append("opponent: " + ", ".join(ds.teams[i]["name"] for i in opp_ids))
        if coaches:
            f["coach_ids"] = [c["id"] for c in coaches]
            filters_h.append("under coach: " + ", ".join(c["name"] for c in coaches))

        # --- entity type ---
        if re.search(r"\b(coaches|coach)\b", mt) and not coaches:
            q["entity"] = "coach"
        elif re.search(r"\b(lineups?|starting fives?|starting lineups?)\b", mt):
            q["entity"] = "lineup"
        elif re.search(r"\b(teams?|franchises?)\b", mt) and not subj_players:
            q["entity"] = "team"
        elif coaches and not subj_players and not subj_teams:
            q["entity"] = "team"
        if q["entity"] == "player":
            for word, pos in POSITION_WORDS.items():
                if re.search(r"\b" + word + r"\b", mt):
                    q["position"] = pos; filters_h.append(f"position: {pos}"); break
        subject_ids: List[int] = []
        if q["entity"] == "player":
            subject_ids = [p["id"] for p in subj_players]
            if subj_teams:
                q["team_ids"] = [tm["id"] for tm in subj_teams]
            if tm_out and not subject_ids and not subj_teams:
                q["team_ids"] = sorted({ds.players[p["id"]]["team_id"] for p in tm_out})
                notes.append("Subjects default to players on the same team as the teammate who is out.")
        elif q["entity"] == "team":
            subject_ids = [tm["id"] for tm in subj_teams]
        elif q["entity"] == "coach":
            subject_ids = [c["id"] for c in coaches]
            q["filters"].pop("coach_ids", None)
        if subject_ids:
            q["ids"] = subject_ids

        # --- metrics + transforms ---
        transform_phrase = None
        if re.search(r"\bper\s*36\b|\bper thirty[- ]six\b", mt):
            transform_phrase = "per36"
        elif re.search(r"\bper\s*100(?:\s*poss(?:essions)?)?\b", mt):
            transform_phrase = "per100"
        elif re.search(r"\b(per game|a game|each game|average|averages|averaged|averaging|avg)\b", mt):
            transform_phrase = "per_game"
        elif re.search(r"\b(total|totals|overall|in total)\b", mt):
            transform_phrase = "raw"
        metric_spans: List[Tuple[int, int]] = []
        found_metrics: List[Tuple[int, str, Optional[str]]] = []
        scrub = re.sub(r"\bper\s*36(?:\s*minutes?)?\b|\bper\s*100(?:\s*poss(?:essions)?)?\b|\bper game\b|\b(?:last|past|previous)\s+(?:\d+|" + "|".join(NUM_WORDS) + r")\s+(?:games?|days?)\b|\bat least \d+ (?:total )?(?:minutes?|games?)\b",
                       lambda m_: " " * len(m_.group(0)), mt)
        for short, (mid, tr) in SHORTHAND.items():
            for mm in re.finditer(r"(?<![a-z0-9])" + short + r"(?![a-z0-9])", scrub):
                found_metrics.append((mm.start(), mid, tr))
                metric_spans.append(mm.span())
        for alias, mid in self.metric_alias:
            for mm in re.finditer(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", scrub):
                s, e = mm.span()
                if any(not (e <= us or s >= ue) for us, ue in metric_spans):
                    continue
                metric_spans.append((s, e))
                found_metrics.append((s, mid, None))
        found_metrics.sort()
        seen = set()
        metric_list: List[Tuple[str, Optional[str]]] = []
        for _, mid, tr in found_metrics:
            if mid in seen:
                continue
            seen.add(mid)
            metric_list.append((mid, tr))
        if re.search(r"\b(project(?:ed|ion|ions)?|next game|forecast)\b", mt) and not any(mid.startswith("proj_") for mid, _ in metric_list):
            for mid, tr in list(metric_list):
                pm = {"pts": "proj_pts", "reb": "proj_reb", "ast": "proj_ast", "min": "proj_min"}.get(mid)
                if pm:
                    metric_list = [(x, y) for x, y in metric_list if x != mid] + [(pm, None)]
            if re.search(r"\bfantasy\b", mt):
                metric_list.append(("proj_fpts", None))
            if not metric_list:
                metric_list = [("proj_min", None), ("proj_pts", None), ("proj_fpts", None)]
        # correlation intent
        corr_intent = re.search(r"\b(correlat\w*|relationship between|association between|related to)\b", mt)
        compare_intent = (len(subject_ids) >= 2 and not corr_intent) or re.search(r"\bcompare\b|\bcomparison\b|\bversus\b|\bvs\b", mt) and len(subject_ids) >= 2
        # sort / limit
        words = set(mt.split())
        asc = bool(words & ASC_WORDS)
        desc = bool(words & DESC_WORDS)
        m = re.search(r"\b(?:top|bottom|best|worst)\s+(\d+|" + "|".join(NUM_WORDS) + r")\b", mt)
        if m:
            q["limit"] = self._num(m.group(1))
        elif (asc or desc) and re.search(r"\b(who|which)\b", mt):
            q["limit"] = 5
        ranking = bool(m) or ((asc or desc) and not subject_ids) or bool(re.search(r"\b(leaders?|rank(?:ing|ed)?|who (?:has|had|scored|averages?))\b", mt) and not subject_ids)
        if subject_ids and not ranking:
            q["limit"] = max(len(subject_ids), 1)

        # build metric entries (apply transforms validly; explain every adjustment)
        unresolved_transform_notes = []
        norm_note_done = False
        entity = q["entity"]
        if corr_intent and len(metric_list) >= 2:
            tokens = []
            for mid, tr in metric_list:
                met = registry.get(mid)
                use = tr or (transform_phrase if transform_phrase in met.transforms and transform_phrase else None)
                if use is None and "per36" in met.transforms and entity == "player" and "game level" not in mt and "game by game" not in mt:
                    use = "per36"
                    notes.append(f"Count metric '{met.name}' is correlated per 36 minutes so playing time does not drive the correlation; edit the query to change.")
                use = use or "raw"
                tokens.append(mid if use == "raw" else f"{mid}_{use}")
            level = "game" if re.search(r"\b(game[- ]by[- ]game|per game level|game level|across games|single[- ]game)\b", mt) else "entity"
            q["correlate"] = {"metrics": tokens, "level": level, "method": "spearman" if "spearman" in mt or "rank" in mt else "pearson"}
            q["metrics"] = []
            q["limit"] = 200
            q["min_games"] = max(q.get("min_games", 1), 10)
            q["min_minutes"] = max(q.get("min_minutes", 0), 200 if entity == "player" else 0)
            q["uncertainty"] = False
            filters_h.append(f"sample guard: at least {q['min_games']} games" + (f" and {int(q['min_minutes'])} minutes" if q["min_minutes"] else "") + " per entity (default; editable)")
        else:
            for mid, tr in metric_list:
                met = registry.get(mid)
                use = tr
                if use is None:
                    use = transform_phrase or ("raw")
                    if transform_phrase and transform_phrase not in met.transforms:
                        if transform_phrase != "raw" and met.unit in ("count", "minutes") and not norm_note_done:
                            unresolved_transform_notes.append(f"'{met.name}' cannot be shown {transform_phrase.replace('_', ' ')}; showing it as-is.")
                        elif transform_phrase != "raw" and not norm_note_done and met.unit not in ("count", "minutes"):
                            unresolved_transform_notes.append("Normalization (per game / per 36 / per 100) applies to count metrics only; percentages, ratios and ratings are computed from window totals and shown as-is.")
                            norm_note_done = True
                        use = "raw"
                    if entity != "player" and use == "per36":
                        unresolved_transform_notes.append("Per-36 is only defined for players; showing per game for teams/coaches/lineups.")
                        use = "per_game" if "per_game" in met.transforms else "raw"
                q["metrics"].append({"id": mid, "transform": use})
            notes += unresolved_transform_notes

        if compare_intent and subject_ids:
            q["compare"] = True
            if not q["metrics"] and "correlate" not in q:
                dflt = [("pts", "per_game"), ("reb", "per_game"), ("ast", "per_game"), ("ts_pct", "raw"), ("usg_pct", "raw"), ("min", "per_game")] if entity == "player" \
                    else [("ortg", "raw"), ("drtg", "raw"), ("nrtg", "raw"), ("pace", "raw"), ("win_pct", "raw")]
                q["metrics"] = [{"id": a, "transform": b} for a, b in dflt]
                notes.append("No metrics named; used a default comparison set.")
        if not q["metrics"] and "correlate" not in q:
            if subject_ids and not ranking:
                q["metrics"] = [{"id": "pts", "transform": "per_game"}, {"id": "reb", "transform": "per_game"}, {"id": "ast", "transform": "per_game"}] if entity == "player" \
                    else [{"id": "ortg", "transform": "raw"}, {"id": "drtg", "transform": "raw"}, {"id": "nrtg", "transform": "raw"}]
                notes.append("No metric named; showing a default summary.")
            else:
                return self._fail(raw, "I could not find a supported metric in that question.", [t])
        # sort
        if q["metrics"] and not q.get("correlate"):
            first = q["metrics"][0]
            met = registry.get(first["id"])
            by = first["id"] if first["transform"] == "raw" else f"{first['id']}_{first['transform']}"
            descending = True
            if asc and not desc:
                descending = False
            elif desc and met.better == "lower" and ("best" in words or "top" in words):
                descending = False
                notes.append(f"'{met.name}' is better when lower, so 'best' sorts ascending.")
            q["sort"] = {"by": by, "desc": descending}
        # situational slices make samples small: default count metrics to per game and relax the guard
        situational = bool(f.get("teammates_out") or f.get("teammates_in") or f.get("opponents") or f.get("b2b") or q["window"].get("last_n"))
        if ranking and situational and not transform_phrase:
            for m_ in q["metrics"]:
                if "per_game" in registry.get(m_["id"]).transforms and m_["transform"] == "raw":
                    m_["transform"] = "per_game"
                    q["sort"]["by"] = f"{m_['id']}_per_game" if q.get("sort", {}).get("by") == m_["id"] else q["sort"].get("by")
            notes.append("Situational ranking: count metrics default to per game (editable).")
        if ranking and q["entity"] == "player" and "min_minutes" not in q:
            lastn = q["window"].get("last_n")
            if situational:
                q["min_games"] = max(q.get("min_games", 1), 2)
                filters_h.append(f"sample guard: at least {q['min_games']} games, at least 12 minutes per game (situational slice - small samples; see sample size)")
            else:
                q["min_games"] = max(q.get("min_games", 1), min(5, lastn) if lastn else 5)
                q["min_minutes"] = 100.0
                filters_h.append(f"sample guard: at least {q['min_games']} games, 100 total minutes and 12 minutes per game (default for rankings; editable)")
            q["min_mpg"] = 12.0
        elif ranking and q["entity"] != "player":
            q["min_games"] = max(q.get("min_games", 1), 2 if situational else 5)

        # leftover text we did not understand (informational)
        covered = list(used) + list(metric_spans)
        rest = list(mt)
        for s, e in metric_spans:
            rest[s:e] = " " * (e - s)
        leftover = [w for w in "".join(rest).split() if w not in _STOP and not w.isdigit() and not re.match(r"\d{4}-\d{2}-\d{2}", w)]
        resolved = {"entities": [{"kind": e["kind"], "id": e["id"], "name": e["name"]} for e in players + teams_mentioned + coaches],
                    "metrics": [{"id": m_["id"], "transform": m_["transform"], "name": registry.get(m_["id"]).name} for m_ in q["metrics"]],
                    "filters": filters_h}
        if q.get("correlate"):
            resolved["metrics"] = [{"id": tok, "transform": "(correlation input)", "name": tok} for tok in q["correlate"]["metrics"]]
        try:
            qn = self.engine.normalize(q)
        except QueryError as e:
            return self._fail(raw, str(e), leftover)
        return {"ok": True, "text": raw, "query": qn, "interpretation": resolved, "notes": notes, "unresolved": leftover,
                "ambiguities": [], "mode": "deterministic-grammar"}

    # ---------------------------------------------------------------------------------------------
    def answer(self, text: str, support_rows: int = 25) -> dict:
        p = self.parse(text)
        if not p["ok"]:
            return p
        return self.execute(p, support_rows)

    def execute(self, parsed: dict, support_rows: int = 25) -> dict:
        q = parsed["query"]
        try:
            res = self.engine.run(q)
        except QueryError as e:
            return {**parsed, "ok": False, "error": str(e), "message": str(e)}
        return {"ok": True, "text": parsed.get("text"), "query": q, "result": res,
                "headline": self.headline(q, res), "inspector": self.inspector(parsed, res, support_rows)}

    def rerun(self, query: dict, support_rows: int = 25) -> dict:
        """Run an (edited) structured query; the inspector is regenerated from the new run."""
        parsed = {"ok": True, "text": "(edited structured query)", "query": self.engine.normalize(query),
                  "interpretation": {"entities": [], "metrics": [], "filters": []}, "notes": ["Query was edited manually and re-run."], "unresolved": []}
        return self.execute(parsed, support_rows)

    # ---------------------------------------------------------------------------------------------
    def inspector(self, parsed: dict, res: dict, support_rows: int) -> dict:
        q = res["query"]
        ds = self.ds
        entity = q["entity"]
        joins = {"player": ["box (player-game rows)", "games (date, season, home/away, opponent, OT)", "team-game context (team + opponent totals, possessions estimate, rest days)", "players (name, team, position)"],
                 "team": ["team-game rows (sum of player box rows)", "games", "opponent team-game rows (for opponent stats and possessions)"],
                 "coach": ["team-game rows grouped by coach_id (coaching eras)", "games", "coaches"],
                 "lineup": ["team-game rows grouped by STARTING lineup (not on-court lineups; no play-by-play in this dataset)", "games"]}[entity]
        sample = {"rows_matched": res["meta"]["rows_matched"], "entities_matched": res["meta"]["entities_matched"],
                  "shown": len(res["rows"]), "games_by_top_row": res["rows"][0]["games"] if res["rows"] else 0}
        support = None
        if res["rows"] and support_rows:
            support = self.engine.support(q, res["rows"][0]["id"], support_rows)
        win = q["window"]
        window_h = []
        if win.get("seasons"):
            window_h.append("seasons: " + ", ".join(win["seasons"]))
        else:
            window_h.append("all seasons in the dataset: " + ", ".join(ds.seasons))
        for k in ("date_from", "date_to"):
            if win.get(k):
                window_h.append(f"{k.replace('_', ' ')}: {win[k]}")
        if win.get("last_n"):
            window_h.append(f"each entity's last {win['last_n']} games within the filters")
        return {
            "original_text": parsed.get("text"), "mode": parsed.get("mode", "structured"),
            "interpretation": parsed.get("interpretation"), "notes": parsed.get("notes", []), "unresolved_words": parsed.get("unresolved", []),
            "window": window_h, "filters": parsed.get("interpretation", {}).get("filters", []) or self._filters_h(q),
            "formulas": [{"label": c["label"], "formula": c["formula"], "version": c["formula_version"], "kind": c["kind"], "unit": c["unit"],
                          "dependencies": c["dependencies"], "source": c["source"]} for c in res["columns"]],
            "joins": joins,
            "sources": [{"dataset": ds.label(), "synthetic": ds.synthetic, "note": ds.meta.get("dataset_note", ""), "last_game_date": res["meta"]["last_game_date"]}],
            "sample_size": sample,
            "uncertainty": {"method": "Bootstrap over the entity's games (300 resamples, seeded from the query hash), 5th-95th percentile; metrics are recomputed from resampled window sums.",
                            "available": any("ci90" in r for r in res["rows"])},
            "warnings": res["meta"]["warnings"],
            "possession_basis": res["meta"]["possession_basis"],
            "supporting_games": support, "structured_query": q, "query_hash": res["query_hash"],
            "editable": True,
        }

    def _filters_h(self, q) -> List[str]:
        out = []
        f = q["filters"]
        for k, v in f.items():
            out.append(f"{k}: {v}")
        if q.get("min_games", 1) > 1:
            out.append(f"min games: {q['min_games']}")
        if q.get("min_minutes"):
            out.append(f"min total minutes: {q['min_minutes']}")
        return out

    def headline(self, q: dict, res: dict) -> str:
        rows, cols = res["rows"], res["columns"]
        syn = " (SYNTHETIC demo data)" if res["meta"]["synthetic"] else ""
        if q.get("correlate") and res.get("correlation"):
            c = res["correlation"]
            r = c["r"][0][1]
            if r is None:
                return "Not enough variation to compute that correlation." + syn
            return (f"{c['method'].title()} correlation between {c['metrics'][0]} and {c['metrics'][1]} is {r:+.2f} over "
                    f"{c['n'][0][1]} {c['unit']} (p={c['p_value'][0][1]:.2g}); descriptive only." + syn)
        if not rows:
            return "No rows matched that question under the stated filters and sample-size guards (see the inspector; loosen min_games / min_mpg to widen)." + syn
        first = cols[0]["label"] if cols else None
        top = rows[0]
        v = top["values"].get(first)
        ci = (top.get("ci90") or {}).get(first)
        ci_t = f" (90% interval {ci[0]:.2f} to {ci[1]:.2f})" if ci else ""
        if q.get("compare"):
            return "Comparison of " + ", ".join(r["name"] for r in rows) + syn
        if len(rows) == 1:
            return f"{top['name']}: {first} = {v:.2f} over {top['games']} games{ci_t}." + syn if v is not None else f"{top['name']}: no value for {first}." + syn
        return f"Top result: {top['name']} with {first} = {v:.2f} over {top['games']} games{ci_t}; {len(rows)} rows shown." + syn if v is not None else f"{len(rows)} rows shown." + syn

    # ---------------------------------------------------------------------------------------------
    def _cand(self, kind: str, eid: int) -> dict:
        ds = self.ds
        if kind == "player":
            p = ds.players[eid]
            return {"id": eid, "name": p["name"], "team": ds.teams[p["team_id"]]["abbr"]}
        if kind == "team":
            return {"id": eid, "name": ds.teams[eid]["name"]}
        return {"id": eid, "name": ds.coaches[eid]["name"]}

    def _num(self, s: str) -> int:
        return int(s) if s.isdigit() else NUM_WORDS[s]

    def _fail(self, raw, message, leftover, ambiguities=None):
        return {"ok": False, "text": raw, "message": message, "unresolved": leftover, "ambiguities": ambiguities or [],
                "suggestions": EXAMPLES, "mode": "deterministic-grammar",
                "note": "No answer was produced because nothing is invented: rephrase, or use the structured query editor."}


_STOP = set("the a an of in on for and or to by with at is are was were who which what how do does did has have had me show give list me their his her its "
            "players player teams team this that these those all any each per as be been being it from over under than more most best top highest lowest worst "
            "fewest least leaders leading compare comparison between against vs versus when without games game season seasons last previous current minutes minute "
            "total totals average averages averaged avg first second third rank ranked ranking by tell find get can you please about out not x".split())


def subj_players_exist(players, p) -> bool:
    return len(players) >= 2
