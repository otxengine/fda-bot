from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup

from connectors.base import BaseNormalizer, ConnectorResult
from ingestion.models import FomcEvent

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_MONTH_NUM = {name: i + 1 for i, name in enumerate(_MONTHS)}
_YEAR_RE = re.compile(r"(\d{4})\s+FOMC Meetings", re.IGNORECASE)
_DATE_RE = re.compile(rf"\b({'|'.join(_MONTHS)})\s+(\d{{1,2}})(?:-(\d{{1,2}}))?")


class FederalReserveNormalizer(BaseNormalizer):
    """Best-effort text scan for meeting dates under each '<year> FOMC
    Meetings' heading. Returns [] (never raises) if the page layout no
    longer matches — see the connector's docstring."""

    def normalize(self, result: ConnectorResult) -> list[FomcEvent]:
        if result.status != "ok" or not result.raw_payload:
            return []
        try:
            soup = BeautifulSoup(result.raw_payload, "html.parser")
            text = soup.get_text("\n")
        except Exception:  # noqa: BLE001 — best-effort scraper, degrade to empty
            return []

        events: list[FomcEvent] = []
        current_year: int | None = None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            year_match = _YEAR_RE.search(line)
            if year_match:
                current_year = int(year_match.group(1))
                continue
            if current_year is None:
                continue
            for m in _DATE_RE.finditer(line):
                month_name, start_day, end_day = m.group(1), m.group(2), m.group(3)
                try:
                    meeting_date = date(current_year, _MONTH_NUM[month_name], int(start_day))
                except ValueError:
                    continue
                events.append(FomcEvent(meeting_date=meeting_date, is_two_day=end_day is not None))
        return events
