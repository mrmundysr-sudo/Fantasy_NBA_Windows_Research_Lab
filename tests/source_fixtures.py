"""SHAPE fixtures: payloads laid out like the documented stats.nba.com / cdn.nba.com / vendor responses, with INVENTED values for
a fictional 4-team mini league. They exercise parsing and import code paths; they are not recordings of real responses and prove
nothing about what the live services return today (that is what the live probe is for)."""
import json, os, random

with open(os.path.join(os.path.dirname(__file__), "..", "nbalab", "sources", "nba_stats_catalog.json"), encoding="utf-8") as _f:
    _CAT = json.load(_f)["endpoints"]
TEAMS = [(1610612901, "AAA", "Alpha", "Ashford"), (1610612902, "BBB", "Bravo", "Brookdale"), (1610612903, "CCC", "Charlie", "Carlton"), (1610612904, "DDD", "Delta", "Dunmore")]
POS = ["G", "G-F", "F", "F-C", "C"]


def players():
    out = []
    pid = 9000001
    for t, abbr, nm, city in TEAMS:
        for i in range(9):
            out.append({"pid": pid, "first": f"Test{abbr}{i}", "last": f"Player{i}", "team": t, "abbr": abbr, "pos": POS[i % 5], "ht": f"6-{i + 1}"})
            pid += 1
    return out


def legacy(name, headers, rows):
    return {"resource": "x", "parameters": {}, "resultSets": [{"name": name, "headers": headers, "rowSet": rows}]}


def player_index():
    hdr = _CAT["playerindex"]["datasets"]["PlayerIndex"]
    rows = []
    for p in players():
        r = dict.fromkeys(hdr)
        r.update(PERSON_ID=p["pid"], PLAYER_LAST_NAME=p["last"], PLAYER_FIRST_NAME=p["first"], TEAM_ID=p["team"], TEAM_ABBREVIATION=p["abbr"], POSITION=p["pos"], HEIGHT=p["ht"], ROSTER_STATUS=1, IS_DEFUNCT=0)
        rows.append([r[h] for h in hdr])
    return legacy("PlayerIndex", hdr, rows)


def games(n_final=12, n_sched=2):
    gs = []
    rnd = random.Random(1)
    day = 22
    for k in range(n_final + n_sched):
        a, b = TEAMS[(k * 2) % 4], TEAMS[(k * 2 + 1 + (k // 2) % 2) % 4]
        if a == b:
            b = TEAMS[(TEAMS.index(a) + 1) % 4]
        gs.append({"gid": f"00226{k + 1:05d}", "date": f"2026-10-{day + k // 2:02d}", "home": a, "away": b, "final": k < n_final, "hs": rnd.randint(95, 125), "as": rnd.randint(95, 125)})
    return gs


def schedule():
    by = {}
    for g in games():
        by.setdefault(g["date"], []).append({
            "gameId": g["gid"], "gameStatus": 3 if g["final"] else 1, "gameDateEst": g["date"] + "T00:00:00Z", "gameDateTimeUTC": g["date"] + "T23:30:00Z", "gameLabel": "",
            "homeTeam": {"teamId": g["home"][0], "teamName": g["home"][2], "teamCity": g["home"][3], "teamTricode": g["home"][1], "score": g["hs"] if g["final"] else 0},
            "awayTeam": {"teamId": g["away"][0], "teamName": g["away"][2], "teamCity": g["away"][3], "teamTricode": g["away"][1], "score": g["as"] if g["final"] else 0},
            "broadcasters": {}, "pointsLeaders": []})
    return {"meta": {}, "leagueSchedule": {"seasonYear": "2026-27", "leagueId": "00", "gameDates": [{"gameDate": d, "games": gs} for d, gs in sorted(by.items())], "weeks": []}}


def _box_line(rnd, mins):
    fga = max(0, int(mins * rnd.uniform(0.25, 0.5)))
    fgm = int(fga * rnd.uniform(0.35, 0.55))
    fg3a = int(fga * rnd.uniform(0.2, 0.5)); fg3m = min(fgm, int(fg3a * rnd.uniform(0.25, 0.4)))
    fta = int(mins * rnd.uniform(0.05, 0.2)); ftm = int(fta * 0.78)
    oreb, dreb = int(mins * rnd.uniform(0, 0.1)), int(mins * rnd.uniform(0.05, 0.2))
    return dict(MIN=round(mins, 1), FGM=fgm, FGA=fga, FG3M=fg3m, FG3A=fg3a, FTM=ftm, FTA=fta, OREB=oreb, DREB=dreb, REB=oreb + dreb, AST=int(mins * 0.1), STL=int(mins * 0.03),
                BLK=int(mins * 0.02), TOV=int(mins * 0.07), PF=int(mins * 0.08), PTS=2 * (fgm - fg3m) + 3 * fg3m + ftm, PLUS_MINUS=rnd.randint(-12, 12))


def player_game_logs():
    hdr = _CAT["playergamelogs"]["datasets"]["PlayerGameLogs"]
    rnd = random.Random(2)
    rows = []
    byteam = {}
    for p in players():
        byteam.setdefault(p["team"], []).append(p)
    for g in games():
        if not g["final"]:
            continue
        for side, opp in (("home", "away"), ("away", "home")):
            t = g[side]
            for i, p in enumerate(byteam[t[0]]):
                if i == 8 and (int(g["gid"][-2:]) % 3 == 0):
                    continue                                  # a DNP: no row at all
                mins = [34, 32, 30, 28, 26, 20, 15, 10, 4][i] + rnd.uniform(-3, 3)
                r = dict.fromkeys(hdr)
                r.update(SEASON_YEAR="2026-27", PLAYER_ID=p["pid"], PLAYER_NAME=f"{p['first']} {p['last']}", TEAM_ID=t[0], TEAM_ABBREVIATION=t[1], TEAM_NAME=t[2], GAME_ID=g["gid"],
                         GAME_DATE=g["date"] + "T00:00:00", MATCHUP=f"{t[1]} vs. {g[opp][1]}" if side == "home" else f"{t[1]} @ {g[opp][1]}", WL="W", **_box_line(rnd, mins))
                rows.append([r[h] for h in hdr])
    return legacy("PlayerGameLogs", hdr, rows)


def team_game_logs():
    hdr = _CAT["teamgamelogs"]["datasets"]["TeamGameLogs"]
    return legacy("TeamGameLogs", hdr, [])


def box_score_v3(gid="002260000 1".replace(" ", "")):
    def side(t, starters):
        pl = []
        for i, p in enumerate([q for q in players() if q["team"] == t[0]][:8]):
            pl.append({"personId": p["pid"], "firstName": p["first"], "familyName": p["last"], "nameI": "x", "position": "G" if i < starters else "", "comment": "" if i < 7 else "DNP - Injury/Illness - Left Ankle; Sprain",
                       "jerseyNum": str(i), "statistics": {"minutes": "30:30" if i < 7 else "", "points": 10, "fieldGoalsMade": 4, "fieldGoalsAttempted": 9, "threePointersMade": 1, "threePointersAttempted": 3,
                                                           "freeThrowsMade": 1, "freeThrowsAttempted": 2, "reboundsOffensive": 1, "reboundsDefensive": 3, "reboundsTotal": 4, "assists": 2, "steals": 1, "blocks": 0,
                                                           "turnovers": 1, "foulsPersonal": 2, "plusMinusPoints": 3}})
        return {"teamId": t[0], "teamTricode": t[1], "teamCity": t[3], "teamName": t[2], "players": pl}
    return {"boxScoreTraditional": {"gameId": gid, "homeTeamId": TEAMS[0][0], "awayTeamId": TEAMS[1][0], "homeTeam": side(TEAMS[0], 5), "awayTeam": side(TEAMS[1], 5)}}


def live_scoreboard():
    return {"scoreboard": {"gameDate": "2026-10-25", "games": [{"gameId": "0022600099", "gameStatus": 1, "gameStatusText": "7:00 pm ET", "gameTimeUTC": "2026-10-25T23:00:00Z",
                                                                "homeTeam": {"teamId": TEAMS[0][0], "teamTricode": "AAA", "score": 0}, "awayTeam": {"teamId": TEAMS[1][0], "teamTricode": "BBB", "score": 0}}]}}


def live_box():
    def team(t):
        pl = []
        for i, p in enumerate([q for q in players() if q["team"] == t[0]][:8]):
            pl.append({"personId": p["pid"], "name": f"{p['first']} {p['last']}", "position": "G" if i < 5 else "", "starter": "1" if i < 5 else "0", "status": "ACTIVE" if i < 7 else "INACTIVE",
                       "notPlayingReason": "" if i < 7 else "INACTIVE_INJURY", "notPlayingDescription": "" if i < 7 else "Injury/Illness - Left Ankle; Sprain", "statistics": {"minutes": "PT00M00.00S", "points": 0}})
        return {"teamId": t[0], "teamTricode": t[1], "players": pl}
    return {"game": {"gameId": "0022600099", "gameStatusText": "Pre-game", "homeTeam": team(TEAMS[0]), "awayTeam": team(TEAMS[1])}}


def bdl_injuries():
    p = players()[3]
    return {"data": [{"player": {"id": 77, "first_name": p["first"], "last_name": p["last"], "position": "G", "team": {"id": 1, "abbreviation": p["abbr"]}},
                      "status": "Day-To-Day", "return_date": "Nov 3", "description": "Left ankle sprain"}], "meta": {"per_page": 100}}


def sdio_injuries():
    p = players()[4]
    return [{"PlayerID": 20001, "FirstName": p["first"], "LastName": p["last"], "Team": p["abbr"], "InjuryStatus": "Out", "InjuryBodyPart": "Knee", "InjuryNotes": "Meniscus", "Updated": "2026-10-25T10:03:11"},
            {"PlayerID": 20002, "FirstName": "Healthy", "LastName": "Guy", "Team": "AAA", "InjuryStatus": None}]
