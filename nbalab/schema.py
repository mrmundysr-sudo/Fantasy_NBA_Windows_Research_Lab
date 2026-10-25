"""Column definitions and SQLite DDL shared by the store, generator and engine."""

# Raw per-player-game box score columns (observed or tracking)
BOX_COLS = ["min", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb",
            "ast", "stl", "blk", "tov", "pf", "pm", "touches", "drives", "pot_ast", "contested"]
# Team/opponent context joined onto each player-game row by the dataset builder
CTX_COLS = ["tm_min", "tm_fga", "tm_fta", "tm_tov", "tm_fgm", "tm_reb", "tm_oreb", "tm_dreb",
            "tm_poss", "tm_pts", "opp_poss", "opp_pts", "opp_reb", "opp_oreb", "opp_dreb",
            "opp_fga", "opp_fg3a"]
# Row-level derived additive columns
DERIVED_COLS = ["gmsc", "dd", "td", "pie_num", "pie_den", "wins", "gp", "gs"]

SUM_COLUMNS = tuple(BOX_COLS + CTX_COLS + DERIVED_COLS)

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS teams (team_id INTEGER PRIMARY KEY, abbr TEXT, name TEXT, city TEXT,
    conference TEXT, lat REAL, lon REAL, data_label TEXT);
CREATE TABLE IF NOT EXISTS coaches (coach_id INTEGER PRIMARY KEY, name TEXT, data_label TEXT);
CREATE TABLE IF NOT EXISTS players (player_id INTEGER PRIMARY KEY, name TEXT, team_id INTEGER, position TEXT,
    age INTEGER, height_in INTEGER, data_label TEXT);
CREATE TABLE IF NOT EXISTS games (game_id INTEGER PRIMARY KEY, season TEXT, game_date TEXT, home_team_id INTEGER,
    away_team_id INTEGER, home_coach_id INTEGER, away_coach_id INTEGER, status TEXT, home_score INTEGER,
    away_score INTEGER, ot INTEGER, data_label TEXT);
CREATE TABLE IF NOT EXISTS box (game_id INTEGER, player_id INTEGER, team_id INTEGER, started INTEGER,
    min REAL, pts INTEGER, fgm INTEGER, fga INTEGER, fg3m INTEGER, fg3a INTEGER, ftm INTEGER, fta INTEGER,
    oreb INTEGER, dreb INTEGER, reb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER, tov INTEGER, pf INTEGER,
    pm INTEGER, touches INTEGER, drives INTEGER, pot_ast INTEGER, contested INTEGER,
    PRIMARY KEY (game_id, player_id));
CREATE TABLE IF NOT EXISTS availability (game_id INTEGER, team_id INTEGER, player_id INTEGER, status TEXT,
    reason TEXT, PRIMARY KEY (game_id, player_id));
CREATE TABLE IF NOT EXISTS events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, kind TEXT,
    team_id INTEGER, player_id INTEGER, game_id INTEGER, status TEXT, detail TEXT, restriction_min REAL,
    published_at TEXT, retrieved_at TEXT, data_label TEXT, superseded_by INTEGER);
CREATE TABLE IF NOT EXISTS sources (name TEXT PRIMARY KEY, kind TEXT, enabled INTEGER, mode TEXT,
    last_attempt_at TEXT, last_success_at TEXT, last_error TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS predictions (pred_id INTEGER PRIMARY KEY AUTOINCREMENT, game_id INTEGER, player_id INTEGER,
    model_id TEXT, model_version TEXT, made_at TEXT, as_of TEXT, trigger_event_id INTEGER, snapshot_id INTEGER,
    p_play REAL, min_mean REAL, min_p10 REAL, min_p50 REAL, min_p90 REAL, stats_json TEXT, data_label TEXT);
CREATE TABLE IF NOT EXISTS snapshots (snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, game_id INTEGER, player_id INTEGER,
    made_at TEXT, as_of TEXT, features_json TEXT, source_times_json TEXT);
CREATE TABLE IF NOT EXISTS scoring_systems (scoring_id TEXT, version INTEGER, name TEXT, rules_json TEXT, created_at TEXT,
    PRIMARY KEY (scoring_id, version));
CREATE TABLE IF NOT EXISTS model_registry (seq INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT, version TEXT, action TEXT,
    at TEXT, metrics_json TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS workspaces (name TEXT PRIMARY KEY, spec_json TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS synthetic_truth (player_id INTEGER PRIMARY KEY, params_json TEXT);
CREATE INDEX IF NOT EXISTS ix_box_player ON box(player_id);
CREATE INDEX IF NOT EXISTS ix_games_date ON games(game_date);
CREATE INDEX IF NOT EXISTS ix_events_player ON events(player_id);
CREATE INDEX IF NOT EXISTS ix_pred_game ON predictions(game_id, player_id);

-- Append-only protection: history must never be silently rewritten.
CREATE TRIGGER IF NOT EXISTS predictions_no_update BEFORE UPDATE ON predictions
BEGIN SELECT RAISE(ABORT, 'predictions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS predictions_no_delete BEFORE DELETE ON predictions
BEGIN SELECT RAISE(ABORT, 'predictions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS snapshots_no_update BEFORE UPDATE ON snapshots
BEGIN SELECT RAISE(ABORT, 'snapshots are append-only'); END;
CREATE TRIGGER IF NOT EXISTS snapshots_no_delete BEFORE DELETE ON snapshots
BEGIN SELECT RAISE(ABORT, 'snapshots are append-only'); END;
CREATE TRIGGER IF NOT EXISTS scoring_no_update BEFORE UPDATE ON scoring_systems
BEGIN SELECT RAISE(ABORT, 'scoring rule versions are immutable; add a new version'); END;
CREATE TRIGGER IF NOT EXISTS scoring_no_delete BEFORE DELETE ON scoring_systems
BEGIN SELECT RAISE(ABORT, 'scoring rule versions are immutable; add a new version'); END;
CREATE TRIGGER IF NOT EXISTS modelreg_no_update BEFORE UPDATE ON model_registry
BEGIN SELECT RAISE(ABORT, 'model registry is append-only'); END;
CREATE TRIGGER IF NOT EXISTS modelreg_no_delete BEFORE DELETE ON model_registry
BEGIN SELECT RAISE(ABORT, 'model registry is append-only'); END;
"""
