"""Replaceable provider adapters.

An adapter turns an external feed (or file, or demo script) into `RawEvent`s. The lab never assumes a
feed is live: each adapter reports a `mode` and the monitor only claims freshness it can verify:

  verified_live  - the adapter fetched a real network source successfully within the freshness window
  simulated      - SYNTHETIC demo script; never presented as real news
  file           - rows loaded from a user-provided file (freshness = file's own publication timestamps)
  not_configured - no credentials / endpoint / permission yet; nothing was fetched

No adapter purchases anything or stores credentials in source control; keys come from environment variables.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class RawEvent:
    source: str
    kind: str                       # injury | lineup | restriction | trade | schedule | result
    status: str                     # out | questionable | probable | available | starting | ...
    published_at: str               # when the SOURCE published it (ISO, Z)
    team_id: Optional[int] = None
    player_id: Optional[int] = None
    game_id: Optional[int] = None
    detail: str = ""
    restriction_min: Optional[float] = None
    retrieved_at: Optional[str] = None  # when WE retrieved it (set by the manager if missing)
    data_label: str = "UNVERIFIED"
    player_name: Optional[str] = None   # unresolved text fields from files / feeds
    team_abbr: Optional[str] = None


class NotConfigured(RuntimeError):
    pass


class ProviderAdapter:
    name = "base"
    kind = "events"
    mode = "not_configured"
    requires_key = False
    notes = ""
    terms_status = "unevaluated"      # unevaluated | verified_private_ok | restricted | unknown

    def configured(self) -> bool:
        return True

    def verify(self) -> dict:
        """Describe what is and is not verified about this adapter (never fetches paid data)."""
        return {"name": self.name, "mode": self.mode, "configured": self.configured(), "requires_key": self.requires_key,
                "terms_status": self.terms_status, "notes": self.notes}

    def fetch(self, since: Optional[str] = None, now: Optional[str] = None) -> List[RawEvent]:  # pragma: no cover - interface
        raise NotImplementedError
