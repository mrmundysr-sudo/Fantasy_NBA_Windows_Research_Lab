"""Bridge: routes `injuries` / `starting_lineups` capability results into the existing EventManager as RawEvents.

Honest timestamps: if the source gives its own update time we use it as `published_at`; otherwise we use our retrieval time and
tag the event `LIVE_RETRIEVED_TIME` so nothing claims the source published it earlier than we saw it.
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..providers.base import NotConfigured, ProviderAdapter, RawEvent
from .base import SourceError

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def _iso_z(ts) -> Optional[str]:
    """Best-effort ISO normalisation. Timestamps without a zone are treated as UTC only if they end with Z; otherwise None
    (we then fall back to retrieval time rather than guess a time zone)."""
    if not ts or not isinstance(ts, str) or not _ISO.match(ts):
        return None
    t = ts.replace(" ", "T")
    if t.endswith("Z"):
        return t[:19] + "Z"
    m = re.search(r"([+-])(\d{2}):?(\d{2})$", t)
    if m:
        from datetime import datetime, timedelta, timezone
        try:
            dt = datetime.fromisoformat(t)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return None
    return None


NBA_LIVE_REASON_STATUS = "out"


class SourceEventAdapter(ProviderAdapter):
    """Wraps one (source, capability) pair as an EventManager provider."""
    kind = "events"

    def __init__(self, router, source: str, cap: str, game_ids=None):
        self.router, self.source, self.cap = router, source, cap
        self.name = f"{source}:{cap}"
        self.game_ids = game_ids or []
        s = router.sources[source]
        self.requires_key = s.requires_key
        self.terms_status = s.terms_status
        self.notes = f"{s.name} {cap}. Disabled until the owner acknowledges the source terms in the Sources panel."

    @property
    def mode(self):  # type: ignore[override]
        s = self.router.sources[self.source]
        if not s.enabled:
            return "not_configured"
        return getattr(self, "_mode", "not_configured")

    def configured(self) -> bool:
        s = self.router.sources[self.source]
        return bool(s.enabled and s.configured())

    def fetch(self, since=None, now=None) -> List[RawEvent]:
        s = self.router.sources[self.source]
        if not s.enabled:
            raise NotConfigured(f"{self.source} is disabled (owner must enable it and acknowledge its terms)")
        if not s.configured():
            raise NotConfigured(f"{self.source} needs {s.key_env}")
        events: List[RawEvent] = []
        if self.source == "NBA_LIVE":
            games = self.router.fetch_from("NBA_LIVE", "live_scoreboard").records
            for g in games:
                if g["status"] == "final":
                    continue
                f = self.router.fetch_from("NBA_LIVE", self.cap, game_id=g["game_id"])
                events += self.to_events(f.records, f.meta["retrieved_at"], game_hint=g["game_id"])
            self._mode = "verified_live"
            return events
        params = {}
        if self.cap == "starting_lineups" and self.source == "SPORTSDATAIO":
            params["date"] = (now or "")[:10] or None
        try:
            f = self.router.fetch_from(self.source, self.cap, **{k: v for k, v in params.items() if v})
        except SourceError as e:
            raise NotConfigured(f"{self.source}:{self.cap}: {e.kind}: {e.detail}")
        self._mode = "verified_live"
        return self.to_events(f.records, f.meta["retrieved_at"])

    def to_events(self, records: List[dict], retrieved_at: str, game_hint=None) -> List[RawEvent]:
        out = []
        for r in records:
            pub = _iso_z(r.get("updated_at"))
            label = "LIVE_SOURCE_TIME" if pub else "LIVE_RETRIEVED_TIME"
            pub = pub or retrieved_at
            if self.cap == "injuries" and self.source != "NBA_LIVE":
                out.append(RawEvent(self.source, "injury", r["status"], pub, detail=r.get("detail", ""), data_label=label, player_name=r.get("player_name"), team_abbr=r.get("team_abbr")))
            elif self.cap == "injuries":      # NBA_LIVE inactives
                out.append(RawEvent(self.source, "injury", NBA_LIVE_REASON_STATUS, pub, detail=(r.get("reason") or "inactive") + " " + (r.get("reason_detail") or ""), data_label=label,
                                    player_name=r.get("player_name"), team_abbr=r.get("team_abbr")))
            elif self.cap == "starting_lineups":
                out.append(RawEvent(self.source, "lineup", "starting", pub, detail=f"starter {r.get('position') or ''}".strip() + (" (confirmed)" if r.get("confirmed") else ""), data_label=label,
                                    player_name=r.get("player_name"), team_abbr=r.get("team_abbr")))
        return out
