"""Event ingestion, source monitoring, freshness and conflict detection.

Policy (documented, visible in the UI):
  * `published_at` (when the source said it) and `retrieved_at` (when we got it) are stored separately.
  * For one player and game, the LATEST PUBLISHED report is used by models. If different sources disagree
    within the conflict window, the pair is flagged as a conflict and surfaced; nothing is silently averaged.
  * A report older than STALE_HOURS relative to the lab clock is flagged stale.
  * Freshness is only ever claimed as `verified_live` for an adapter that actually fetched a live network source.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from .providers.base import NotConfigured, ProviderAdapter, RawEvent
from .store import Store, utcnow

STALE_HOURS = 24.0
FRESH_HOURS = 6.0
CONFLICT_WINDOW_HOURS = 36.0
LIVE_FRESH_MINUTES = 180.0


def _p(ts: str) -> datetime:
    return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")


def hours_between(a: str, b: str) -> float:
    return (_p(b) - _p(a)).total_seconds() / 3600.0


class EventManager:
    def __init__(self, store: Store, ds, fb, clock: Callable[[], str], on_new_events: Optional[Callable[[List[dict], str], dict]] = None):
        self.store, self.ds, self.fb, self.clock = store, ds, fb, clock
        self.adapters: Dict[str, ProviderAdapter] = {}
        self.on_new_events = on_new_events
        self._names = {p["name"].lower(): pid for pid, p in ds.players.items()}
        self._abbr = {t["abbr"].lower(): tid for tid, t in ds.teams.items()}

    # ---------------------------------------------------------------------------------------------
    def register(self, adapter: ProviderAdapter) -> None:
        self.adapters[adapter.name] = adapter
        if not self.store.one("SELECT 1 FROM sources WHERE name=?", (adapter.name,)):
            self.store.execute("INSERT INTO sources (name, kind, enabled, mode, notes) VALUES (?,?,?,?,?)",
                               (adapter.name, adapter.kind, 1 if adapter.configured() else 0, adapter.mode, adapter.notes))

    def poll(self, name: str, recompute: bool = True) -> dict:
        ad = self.adapters[name]
        now = self.clock()
        self.store.execute("UPDATE sources SET last_attempt_at=? WHERE name=?", (utcnow(), name))
        try:
            evs = ad.fetch(None, now)
        except NotConfigured as e:
            self.store.execute("UPDATE sources SET last_error=?, mode=? WHERE name=?", (str(e), "not_configured", name))
            return {"source": name, "ok": False, "error": str(e), "inserted": 0}
        except Exception as e:  # network / parse failures must never crash the lab
            self.store.execute("UPDATE sources SET last_error=? WHERE name=?", (f"{type(e).__name__}: {e}", name))
            return {"source": name, "ok": False, "error": f"{type(e).__name__}: {e}", "inserted": 0}
        res = self.ingest(evs, recompute=recompute)
        self.store.execute("UPDATE sources SET last_success_at=?, last_error=NULL, mode=?, enabled=1 WHERE name=?", (utcnow(), ad.mode, name))
        return {"source": name, "ok": True, **res}

    # ---------------------------------------------------------------------------------------------
    def _resolve(self, ev: RawEvent) -> Optional[str]:
        if ev.player_id is None and ev.player_name:
            pid = self._names.get(ev.player_name.strip().lower())
            if pid is None:
                return f"unknown player '{ev.player_name}'"
            ev.player_id = pid
        if ev.team_id is None and ev.team_abbr:
            tid = self._abbr.get(ev.team_abbr.strip().lower())
            if tid is None:
                return f"unknown team '{ev.team_abbr}'"
            ev.team_id = tid
        if ev.player_id is not None and ev.team_id is None:
            ev.team_id = self.ds.players[ev.player_id]["team_id"]
        if ev.game_id is None and ev.team_id is not None:
            today = self.clock()[:10]
            ups = sorted((g["game_date"], gid) for gid, g in self.ds.games.items()
                         if g["status"] == "scheduled" and g["game_date"] >= today and ev.team_id in (g["home_team_id"], g["away_team_id"]))
            if not ups:
                return "no upcoming scheduled game for that team"
            ev.game_id = ups[0][1]
        if ev.team_id is None:
            return "event has no team or player"
        return None

    def ingest(self, events: List[RawEvent], recompute: bool = True) -> dict:
        retrieved = self.clock()
        inserted, dups, rejected = [], 0, []
        for ev in events:
            err = self._resolve(ev)
            if err:
                rejected.append({"source": ev.source, "reason": err})
                continue
            exists = self.store.one("SELECT event_id FROM events WHERE source=? AND IFNULL(player_id,-1)=IFNULL(?,-1) AND IFNULL(game_id,-1)=IFNULL(?,-1) "
                                    "AND status=? AND published_at=?", (ev.source, ev.player_id, ev.game_id, ev.status, ev.published_at))
            if exists:
                dups += 1
                continue
            cur = self.store.execute(
                "INSERT INTO events (source, kind, team_id, player_id, game_id, status, detail, restriction_min, published_at, retrieved_at, data_label) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ev.source, ev.kind, ev.team_id, ev.player_id, ev.game_id, ev.status, ev.detail, ev.restriction_min, ev.published_at, ev.retrieved_at or retrieved, ev.data_label))
            inserted.append({"event_id": cur.lastrowid, "game_id": ev.game_id, "team_id": ev.team_id, "player_id": ev.player_id, "status": ev.status})
        out = {"inserted": len(inserted), "duplicates": dups, "rejected": rejected, "events": inserted, "recomputed": []}
        if inserted:
            self.fb.refresh_events()
            if recompute and self.on_new_events:
                out["recomputed"] = self.on_new_events(inserted, retrieved)
        return out

    # ---------------------------------------------------------------------------------------------
    def recent_events(self, limit: int = 100, game_id: Optional[int] = None) -> List[dict]:
        now = self.clock()
        q = "SELECT * FROM events WHERE kind IN ('injury','restriction','lineup') " + ("AND game_id=? " if game_id else "") + "ORDER BY published_at DESC, event_id DESC LIMIT ?"
        rows = self.store.query(q, (game_id, limit) if game_id else (limit,))
        out = []
        for r in rows:
            d = dict(r)
            d["player_name"] = self.ds.players[r["player_id"]]["name"] if r["player_id"] in self.ds.players else None
            d["team"] = self.ds.teams[r["team_id"]]["abbr"] if r["team_id"] in self.ds.teams else None
            d["age_hours"] = round(hours_between(r["published_at"], now), 1) if r["published_at"] else None
            d["stale"] = bool(d["age_hours"] is not None and d["age_hours"] >= STALE_HOURS)
            d["synthetic"] = (r["data_label"] or "").startswith("SYN")
            out.append(d)
        return out

    def conflicts(self, game_ids: Optional[List[int]] = None) -> List[dict]:
        now = self.clock()
        where = "WHERE game_id IN (%s) AND " % ",".join("?" * len(game_ids)) if game_ids else "WHERE game_id IN (SELECT game_id FROM games WHERE status='scheduled') AND "
        rows = self.store.query(f"SELECT * FROM events {where} kind IN ('injury','restriction') AND player_id IS NOT NULL ORDER BY published_at", tuple(game_ids or []))
        by = defaultdict(lambda: defaultdict(list))
        for r in rows:
            by[(r["game_id"], r["player_id"])][r["source"]].append(r)
        out = []
        for (gid, pid), srcs in by.items():
            if len(srcs) < 2:
                continue
            latest = {s: max(v, key=lambda r: (r["published_at"], r["event_id"])) for s, v in srcs.items()}
            statuses = {r["status"] for r in latest.values()}
            if len(statuses) < 2:
                continue
            times = sorted(r["published_at"] for r in latest.values())
            if hours_between(times[0], times[-1]) > CONFLICT_WINDOW_HOURS:
                continue
            winner = max(latest.values(), key=lambda r: (r["published_at"], r["event_id"]))
            out.append({"game_id": gid, "player_id": pid, "player": self.ds.players[pid]["name"],
                        "reports": [{"source": s, "status": r["status"], "published_at": r["published_at"], "detail": r["detail"]} for s, r in latest.items()],
                        "resolution": f"latest published wins: '{winner['status']}' from {winner['source']} at {winner['published_at']}", "synthetic": (winner["data_label"] or "").startswith("SYN")})
        return out

    def source_status(self) -> List[dict]:
        now_wall = utcnow()
        out = []
        for r in self.store.query("SELECT * FROM sources ORDER BY name"):
            ad = self.adapters.get(r["name"])
            n = self.store.one("SELECT COUNT(*) c, MAX(published_at) lp FROM events WHERE source=?", (r["name"],))
            last_pub = n["lp"]
            if ad and ad.mode == "simulated" or (r["mode"] == "simulated"):
                claim = "simulated"
            elif r["mode"] == "verified_live" and r["last_success_at"]:
                age_min = hours_between(r["last_success_at"], now_wall) * 60
                claim = "verified_live" if age_min <= LIVE_FRESH_MINUTES else "stale"
            elif r["mode"] == "file":
                claim = "file"
            elif r["last_success_at"] is None and r["mode"] in ("not_configured", None):
                claim = "not_configured"
            else:
                claim = "unverified"
            out.append({"name": r["name"], "kind": r["kind"], "mode": r["mode"], "freshness_claim": claim, "events_stored": n["c"], "latest_published": last_pub,
                        "latest_published_age_hours": round(hours_between(last_pub, self.clock()), 1) if last_pub else None,
                        "last_attempt_at": r["last_attempt_at"], "last_success_at": r["last_success_at"], "last_error": r["last_error"], "notes": r["notes"],
                        "terms_status": getattr(ad, "terms_status", "unknown") if ad else "unknown"})
        return out

    def team_input_freshness(self, game_id: int, team_id: int) -> dict:
        now = self.clock()
        rows = self.store.query("SELECT published_at, source, data_label FROM events WHERE game_id=? AND team_id=? ORDER BY published_at DESC LIMIT 1", (game_id, team_id))
        if not rows:
            return {"state": "no_report", "note": "No injury/lineup report has been published for this team and game in the stored data."}
        age = hours_between(rows[0]["published_at"], now)
        st = "fresh" if age <= FRESH_HOURS else ("aging" if age < STALE_HOURS else "stale")
        return {"state": st, "latest_report_age_hours": round(age, 1), "source": rows[0]["source"], "published_at": rows[0]["published_at"],
                "simulated": (rows[0]["data_label"] or "").startswith("SYN")}
