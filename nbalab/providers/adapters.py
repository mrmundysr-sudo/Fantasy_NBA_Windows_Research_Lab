"""Concrete adapters: synthetic demo script, CSV file feed, and a generic configurable HTTP JSON feed."""
from __future__ import annotations

import csv
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .base import NotConfigured, ProviderAdapter, RawEvent


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")


class DemoScriptProvider(ProviderAdapter):
    """SYNTHETIC injury/lineup scenario for demonstrating ingestion, conflicts, staleness and recompute.

    Reports are scripted against the lab's demo clock and the synthetic upcoming schedule. They are not news.
    """
    name = "DEMO_SCRIPT"
    mode = "simulated"
    notes = "Scripted SYNTHETIC reports for demonstration. Never real information."
    terms_status = "not_applicable"

    def __init__(self, ds, store, fb):
        self.ds, self.store, self.fb = ds, store, fb
        self.step_no = 0

    def _targets(self, clock: str):
        ups = sorted((g["game_date"], gid) for gid, g in self.ds.games.items() if g["status"] == "scheduled" and g["game_date"] >= clock[:10])
        picks = []
        for _, gid in ups:
            g = self.ds.games[gid]
            for t in (g["home_team_id"], g["away_team_id"]):
                if all(p[0] != t for p in picks):
                    picks.append((t, gid))
            if len(picks) >= 4:
                break
        return picks

    def _top_players(self, team_id: int, gid: int, n: int = 4):
        d = datetime.strptime(self.ds.games[gid]["game_date"], "%Y-%m-%d").toordinal()
        rows = [(self.fb.h.player_state(p, d).ewm_min, p) for p in self.fb.roster[team_id]]
        return [p for m, p in sorted(rows, reverse=True) if m > 0][:n]

    def script(self, clock: str) -> List[List[RawEvent]]:
        t = _parse(clock)
        tg = self._targets(clock)
        if len(tg) < 3:
            return []
        (t1, g1), (t2, g2), (t3, g3) = tg[0], tg[1], tg[2]
        a, b = self._top_players(t1, g1), self._top_players(t2, g2)
        c = self._top_players(t3, g3)
        L = "SYNTHETIC_DEMO"
        return [
            [RawEvent("DEMO_FEED_A", "injury", "questionable", _iso(t - timedelta(hours=5)), t1, a[0], g1, "ankle soreness (demo)", None, None, L)],
            [RawEvent("DEMO_FEED_B", "injury", "out", _iso(t - timedelta(hours=2)), t1, a[0], g1, "ankle sprain (demo) - conflicts with FEED_A", None, None, L)],
            [RawEvent("DEMO_FEED_A", "restriction", "probable", _iso(t - timedelta(hours=1)), t2, b[1], g2, "minutes restriction ~22 (demo)", 22.0, None, L)],
            [RawEvent("DEMO_FEED_C", "injury", "out", _iso(t - timedelta(hours=60)), t3, c[0], g3, "stale report from 60h ago (demo)", None, None, L)],
            [RawEvent("DEMO_FEED_B", "lineup", "probable", _iso(t - timedelta(minutes=20)), t2, b[0], g2, "confirmed starter (demo)", None, None, L)],
        ]

    def fetch(self, since=None, now=None) -> List[RawEvent]:
        if not now:
            raise NotConfigured("demo script needs the lab clock")
        steps = self.script(now)
        if self.step_no >= len(steps):
            return []
        batch = steps[self.step_no]
        self.step_no += 1
        return batch

    def reset(self):
        self.step_no = 0


class CsvEventProvider(ProviderAdapter):
    """Reads reports the user maintains in a CSV (e.g. transcribed from an official injury report).

    Columns: source,kind,status,published_at,team,player,game_id,detail,restriction_min
    `team` is the team abbreviation; `player` is the full player name; ISO UTC timestamps.
    """
    name = "CSV_FILE"
    mode = "file"
    notes = "User-supplied rows. Freshness is the file's own published_at stamps; provenance is the user's responsibility."
    terms_status = "user_responsibility"

    def __init__(self, path: Optional[str] = None):
        self.path = path

    def configured(self) -> bool:
        return bool(self.path and os.path.exists(self.path))

    def fetch(self, since=None, now=None) -> List[RawEvent]:
        if not self.configured():
            raise NotConfigured("no CSV path configured")
        out = []
        with open(self.path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out.append(RawEvent(source=r.get("source") or "CSV_FILE", kind=r.get("kind") or "injury", status=(r.get("status") or "").lower(),
                                    published_at=r["published_at"], game_id=int(r["game_id"]) if r.get("game_id") else None,
                                    detail=r.get("detail", ""), restriction_min=float(r["restriction_min"]) if r.get("restriction_min") else None,
                                    data_label="USER_FILE", player_name=r.get("player"), team_abbr=r.get("team")))
        return out


class HttpJsonProvider(ProviderAdapter):
    """Generic, disabled-by-default HTTP JSON feed.

    Nothing here is tied to a specific vendor, because no vendor integration has been verified. To use a
    vendor, an owner supplies: endpoint URL, the environment variable holding the API key (never a file in git),
    a field mapping, and confirms the vendor's terms permit this private research use and polling rate.
    Enforces a minimum interval between calls and sends only a User-Agent plus the key header.
    """
    mode = "not_configured"
    requires_key = True
    terms_status = "unevaluated"

    def __init__(self, name: str, url: str = "", key_env: str = "", key_header: str = "Authorization", mapping: Optional[Dict[str, str]] = None,
                 list_path: str = "", min_interval_s: float = 300.0, enabled: bool = False):
        self.name = name
        self.url, self.key_env, self.key_header = url, key_env, key_header
        self.mapping = mapping or {}
        self.list_path = list_path
        self.min_interval_s = min_interval_s
        self.enabled = enabled
        self._last = 0.0

    def configured(self) -> bool:
        return bool(self.enabled and self.url and self.mapping and (not self.key_env or os.environ.get(self.key_env)))

    def verify(self) -> dict:
        d = super().verify()
        d["notes"] = ("Not configured. Needs URL, key env var, field mapping, and an owner decision that terms/cost allow it."
                      if not self.configured() else "Configured; first successful fetch marks this source verified_live.")
        return d

    def fetch(self, since=None, now=None) -> List[RawEvent]:
        if not self.configured():
            raise NotConfigured(f"{self.name}: not configured (no purchase or key is ever assumed)")
        if time.time() - self._last < self.min_interval_s:
            raise NotConfigured(f"{self.name}: rate limit - minimum {self.min_interval_s}s between calls")
        req = urllib.request.Request(self.url, headers={"User-Agent": "nbalab-research/0.1"})
        if self.key_env:
            req.add_header(self.key_header, os.environ[self.key_env])
        self._last = time.time()
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - owner-configured URL
            data = json.loads(resp.read().decode("utf-8"))
        items = data
        for part in [p for p in self.list_path.split(".") if p]:
            items = items[part]
        self.mode = "verified_live"
        m = self.mapping
        out = []
        for it in items:
            out.append(RawEvent(source=self.name, kind=it.get(m.get("kind", "kind"), "injury"), status=str(it.get(m["status"], "")).lower(),
                                published_at=str(it[m["published_at"]]), detail=str(it.get(m.get("detail", "detail"), "")),
                                player_name=it.get(m.get("player")), team_abbr=it.get(m.get("team")), data_label="LIVE_UNVERIFIED_SCHEMA"))
        return out
