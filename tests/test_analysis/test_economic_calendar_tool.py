"""Regression test for a real bug found while building this feature: the
economic calendar's events must be ordered by the EVENT date embedded in the
message text, not by flags.created_at (which is fetch time — since a whole
batch of releases is inserted in one fetch, they'd all share nearly the same
created_at and sort in arbitrary/insertion order instead of a real calendar
order)."""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

from analysis.chat_tools import get_economic_calendar
from ingestion.models import EconomicRelease
from storage.db import write_connection
from storage.writers import upsert_economic_releases


def test_calendar_is_sorted_by_event_date_not_insertion_order() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "t.db")
        today = date.today()
        # Insert deliberately OUT of chronological order.
        upsert_economic_releases(conn, [
            EconomicRelease(release_id=53, release_name="GDP", release_date=today + timedelta(days=20)),
            EconomicRelease(release_id=10, release_name="CPI", release_date=today + timedelta(days=5)),
            EconomicRelease(release_id=50, release_name="Employment Situation", release_date=today + timedelta(days=10)),
        ])
        result = get_economic_calendar(conn, days_ahead=30)
        conn.close()

    events = result["upcoming_and_recent_releases"]
    dates = [e["date"] for e in events]
    assert dates == sorted(dates)
    assert "CPI" in events[0]["event"]
    assert "GDP" in events[-1]["event"]


def test_calendar_windows_out_events_beyond_days_ahead() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "t.db")
        today = date.today()
        upsert_economic_releases(conn, [
            EconomicRelease(release_id=10, release_name="CPI", release_date=today + timedelta(days=5)),
            EconomicRelease(release_id=53, release_name="GDP", release_date=today + timedelta(days=200)),
        ])
        result = get_economic_calendar(conn, days_ahead=30)
        conn.close()

    events = result["upcoming_and_recent_releases"]
    assert len(events) == 1
    assert "CPI" in events[0]["event"]
