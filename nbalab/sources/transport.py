"""HTTP transports. The default one is deliberately plain: identifiable client, per-host spacing, one retry on 5xx,
no proxy rotation, no header spoofing beyond the headers a source documents. If a host refuses us, we report 'blocked'
instead of trying to evade it."""
from __future__ import annotations

import gzip
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional

from .base import SourceError


@dataclass
class HttpResult:
    status: int
    body: bytes
    elapsed_ms: float
    url: str

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8", "replace"))
        except ValueError as e:
            head = self.body[:80].decode("utf-8", "replace").replace("\n", " ")
            raise SourceError("parse", f"not JSON ({e}); starts with: {head!r}", self.status)


class UrllibTransport:
    def __init__(self, min_interval_s: float = 1.0, user_agent: str = "nbalab-research/0.1 (private research tool)"):
        self.min_interval_s = min_interval_s
        self.user_agent = user_agent
        self._last: Dict[str, float] = {}

    def get(self, url: str, params: Optional[dict] = None, headers: Optional[dict] = None, timeout: float = 30.0) -> HttpResult:
        if params:
            q = urllib.parse.urlencode([(k, "" if v is None else v) for k, v in sorted(params.items())], doseq=True)
            url = url + ("&" if "?" in url else "?") + q
        host = urllib.parse.urlparse(url).netloc
        wait = self.min_interval_s - (time.time() - self._last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        h = {"User-Agent": self.user_agent, "Accept": "application/json, text/plain, */*", "Accept-Encoding": "gzip"}
        h.update(headers or {})
        last_err: Optional[SourceError] = None
        for attempt in range(2):
            t0 = time.time()
            self._last[host] = t0
            try:
                req = urllib.request.Request(url, headers=h)
                with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https endpoints
                    body = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                    return HttpResult(r.status, body, (time.time() - t0) * 1000, url)
            except urllib.error.HTTPError as e:
                body = e.read() or b""
                if e.code in (401, 402, 403) and len(body) < 2000:
                    last_err = SourceError("auth" if e.code in (401, 402) else "blocked", f"HTTP {e.code}", e.code)
                    break
                if e.code == 429:
                    last_err = SourceError("http", "HTTP 429 rate limited", 429)
                    break
                last_err = SourceError("http", f"HTTP {e.code}", e.code)
                if e.code < 500:
                    break
                time.sleep(1.5)
            except (socket.timeout, TimeoutError):
                last_err = SourceError("timeout", f"no response in {timeout:.0f}s (host may not answer this network)")
                break  # retrying does not help (see nba_api docs)
            except urllib.error.URLError as e:
                last_err = SourceError("unreachable", str(getattr(e, "reason", e)))
                break
        raise last_err  # type: ignore[misc]


class RecordedTransport:
    """Test double: maps a URL substring to a canned body (dict -> JSON) or an exception."""

    def __init__(self, routes: Optional[dict] = None):
        self.routes = routes or {}
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=30.0):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        for needle, resp in self.routes.items():
            if needle in url:
                if isinstance(resp, Exception):
                    raise resp
                body = resp if isinstance(resp, (bytes, bytearray)) else json.dumps(resp).encode()
                return HttpResult(200, bytes(body), 1.0, url)
        raise SourceError("unreachable", "no recorded route for " + url)
