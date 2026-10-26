"""NBA official injury report (PDF published several times a day under official.nba.com / ak-static.cms.nba.com).

Evidence (2026-10-25): PDF URLs of the form  ak-static.cms.nba.com/referee/injury/Injury-Report_YYYY-MM-DD_HH_MMAM.pdf  appear in public search
results with times such as 07_30AM, 11_30AM, 01_15PM, 02_45PM, 04_30PM, 08_00PM (so releases are NOT on a fixed hourly grid); a sample report
(2026-04-27 08:00 PM) showed columns Game Date, Game Time (ET), Matchup, Team, Player Name, Current Status, Reason, with statuses Available /
Questionable / Probable / Out and reasons like 'Injury/Illness - Left Knee; Sprain'. This is the PRIMARY source for injury status, free and with an
exact publication time encoded in the file name.

Not verified from a live fetch: the exact text layout produced by PDF extraction. Parsing therefore targets the line format seen in that sample
and is covered only by a shape fixture. PDF text extraction needs the optional `pypdf` package (pure Python); without it this source reports
`not_configured` with the install hint rather than failing silently. Terms: the NBA.com Terms of Use may apply to this content as well (unreviewed).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from .base import CapabilityInfo, DataSource, Fetched, SourceError

PDF_BASE = "https://ak-static.cms.nba.com/referee/injury"
LISTING_URLS = ["https://official.nba.com/nba-injury-report-2026-27/", "https://official.nba.com/nba-injury-report-2025-26/"]
STATUSES = ("Available", "Questionable", "Probable", "Doubtful", "Out")
TEAM_NAMES = ["Atlanta Hawks", "Boston Celtics", "Brooklyn Nets", "Charlotte Hornets", "Chicago Bulls", "Cleveland Cavaliers", "Dallas Mavericks", "Denver Nuggets", "Detroit Pistons",
              "Golden State Warriors", "Houston Rockets", "Indiana Pacers", "LA Clippers", "Los Angeles Lakers", "Memphis Grizzlies", "Miami Heat", "Milwaukee Bucks",
              "Minnesota Timberwolves", "New Orleans Pelicans", "New York Knicks", "Oklahoma City Thunder", "Orlando Magic", "Philadelphia 76ers", "Phoenix Suns",
              "Portland Trail Blazers", "Sacramento Kings", "San Antonio Spurs", "Toronto Raptors", "Utah Jazz", "Washington Wizards"]
_GAME = re.compile(r"(\d{2}/\d{2}/\d{4})\s+(\d{1,2}:\d{2})\s*\(ET\)\s+([A-Z]{2,3})@([A-Z]{2,3})\s*(.*)")
_ROW = re.compile(r"^(?P<name>[^,]+,\s*[^,]+?)\s+(?P<status>Available|Questionable|Probable|Doubtful|Out)\b\s*(?P<reason>.*)$")
_STAMP = re.compile(r"Injury Report:\s*(\d{2}/\d{2}/\d{2,4})\s+(\d{1,2}:\d{2})\s*(AM|PM)", re.I)
_FILE = re.compile(r"Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})_(\d{2})(AM|PM)\.pdf")


def _nth_sunday(year: int, month: int, n: int) -> datetime:
    d = datetime(year, month, 1)
    first = d + timedelta(days=(6 - d.weekday()) % 7)
    return first + timedelta(weeks=n - 1)


def et_to_utc(naive_et: datetime) -> datetime:
    """US Eastern -> UTC without tz databases (Windows lacks one by default). DST: 2nd Sunday of March 02:00 to 1st Sunday of Nov 02:00."""
    start = _nth_sunday(naive_et.year, 3, 2).replace(hour=2)
    end = _nth_sunday(naive_et.year, 11, 1).replace(hour=2)
    off = 4 if start <= naive_et < end else 5
    return (naive_et + timedelta(hours=off)).replace(tzinfo=timezone.utc)


def published_from_filename(name: str) -> Optional[str]:
    m = _FILE.search(name)
    if not m:
        return None
    d, hh, mm, ap = m.groups()
    h = int(hh) % 12 + (12 if ap == "PM" else 0)
    dt = datetime.strptime(d, "%Y-%m-%d").replace(hour=h, minute=int(mm))
    return et_to_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def slot_names(now_utc: datetime, back_minutes: int = 360, step: int = 15) -> List[str]:
    """Candidate report file names, newest first, on a 15-minute grid in Eastern time (local release times observed: :00 :15 :30 :45)."""
    out = []
    t = now_utc.replace(second=0, microsecond=0)
    t -= timedelta(minutes=t.minute % step)
    for i in range(back_minutes // step):
        u = t - timedelta(minutes=step * i)
        # convert UTC -> ET by trying both offsets and picking the one that round-trips
        for off in (4, 5):
            et = (u - timedelta(hours=off)).replace(tzinfo=None)
            if et_to_utc(et) == u:
                ap = "AM" if et.hour < 12 else "PM"
                out.append(f"Injury-Report_{et:%Y-%m-%d}_{(et.hour % 12) or 12:02d}_{et.minute:02d}{ap}.pdf")
                break
    return out


def flip_name(n: str) -> str:
    last, first = [x.strip() for x in n.split(",", 1)]
    return f"{first} {last}".strip()


def parse_report_text(text: str, published_at: Optional[str] = None) -> List[dict]:
    """Parse text extracted from one report. State machine: game header lines set date/matchup, team names set the team, rows end in a status."""
    rows, cur_game, cur_team = [], {}, None
    if not published_at:
        m = _STAMP.search(text)
        if m:
            d, t, ap = m.groups()
            fmt = "%m/%d/%y" if len(d) == 8 else "%m/%d/%Y"
            hh, mm = (int(x) for x in t.split(":"))
            dt = datetime.strptime(d, fmt).replace(hour=hh % 12 + (12 if ap.upper() == "PM" else 0), minute=mm)
            published_at = et_to_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("Injury Report:") or line.startswith("Game Date") or re.match(r"Page \d+ of \d+", line):
            continue
        g = _GAME.match(line)
        if g:
            d, t, away, home, line = g.groups()
            cur_game = {"game_date": datetime.strptime(d, "%m/%d/%Y").strftime("%Y-%m-%d"), "game_time_et": t, "away_abbr": away, "home_abbr": home}
            cur_team = None
        for tn in TEAM_NAMES:
            if line.startswith(tn):
                cur_team, line = tn, line[len(tn):].strip()
                break
        if line.upper().startswith("NOT YET SUBMITTED"):
            rows.append({"source": "NBA_INJURY_REPORT", **cur_game, "team_name": cur_team, "player_name": None, "status": "not_submitted", "reason": "NOT YET SUBMITTED", "published_at": published_at})
            continue
        r = _ROW.match(line)
        if r and cur_game:
            rows.append({"source": "NBA_INJURY_REPORT", **cur_game, "team_name": cur_team, "player_name": flip_name(r["name"]), "status": r["status"].lower(), "reason": r["reason"].strip(), "published_at": published_at})
        elif rows and cur_game and line and not _ROW.match(line):
            rows[-1]["reason"] = (rows[-1]["reason"] + " " + line).strip()          # wrapped reason text
    return rows


def pdf_to_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # optional dependency
    except ImportError:
        raise SourceError("not_configured", "PDF text extraction needs the optional 'pypdf' package: pip install pypdf")
    import io
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)


class NbaInjuryReportSource(DataSource):
    name = "NBA_INJURY_REPORT"
    kind = "injuries"
    homepage = "https://official.nba.com/nba-injury-report-2026-27/"
    terms_url = "https://www.nba.com/termsofuse"
    terms_status = "unreviewed"
    terms_summary = ("Official injury-report PDFs on NBA-controlled hosts. The NBA.com Terms of Use (dated 2026-07-13) may apply to this content too; Section 9 restricts "
                     "'NBA Statistics' (see NBA_STATS). Whether an injury report is 'NBA Statistics' is not something this app can decide. Read the terms yourself.")
    min_interval_s = 1.0

    def capabilities(self) -> Dict[str, CapabilityInfo]:
        return {"injuries": CapabilityInfo("injuries", "referee/injury/Injury-Report_<date>_<time>.pdf", "free", "documented",
                                           "Primary source; exact publish time in file name; needs pypdf; text layout parsed from one sample, so shape is unverified")}

    def _fetch(self, cap: str, **params) -> Fetched:
        names: List[str] = []
        now = params.get("now")
        now_dt = datetime.strptime(now[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc) if now else datetime.now(timezone.utc)
        listing_error = None
        for url in LISTING_URLS:
            try:
                res = self.transport.get(url, None, headers={"Accept": "text/html"}, timeout=30.0)
                found = sorted(set(_FILE.findall(res.body.decode("utf-8", "replace")) and re.findall(r"Injury-Report_\d{4}-\d{2}-\d{2}_\d{2}_\d{2}[AP]M\.pdf", res.body.decode("utf-8", "replace"))),
                               key=lambda n: published_from_filename(n) or "", reverse=True)
                if found:
                    names = found[:1]
                    break
            except SourceError as e:
                listing_error = e
        tried = 0
        if not names:
            names = slot_names(now_dt)[: int(params.get("max_slots", 24))]
        data, used, last_err = None, None, listing_error
        for n in names:
            tried += 1
            try:
                res = self.transport.get(f"{PDF_BASE}/{n}", None, headers={"Accept": "application/pdf"}, timeout=30.0)
                data, used = res, n
                break
            except SourceError as e:
                last_err = e
                if e.kind in ("timeout", "unreachable", "blocked"):
                    break
        if data is None:
            raise last_err or SourceError("unreachable", "no report found")
        pub = published_from_filename(used)
        recs = parse_report_text(pdf_to_text(data.body), pub)
        return Fetched(cap, self.name, recs, {"url": data.url, "status": data.status, "elapsed_ms": round(data.elapsed_ms), "rows": len(recs), "file": used, "published_at": pub, "slots_tried": tried,
                                              "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, sample=recs[:3])
