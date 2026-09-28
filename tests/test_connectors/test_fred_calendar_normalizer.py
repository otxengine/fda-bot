"""Unit tests for the FRED economic-release calendar — added as a free,
official substitute for scraping Investing.com's economic calendar (its ToS
explicitly prohibits automated use of its data in writing)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from connectors.base import ConnectorResult
from ingestion.models import EconomicRelease
from ingestion.normalizers.fred_calendar import FredCalendarNormalizer

FIXTURE = {
    "release_dates": [
        {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-10-14"},
        {"release_id": 50, "release_name": "Employment Situation", "date": "2026-10-02"},
        # Not one of our curated indicators — must be filtered out, not error.
        {"release_id": 287, "release_name": "Nikkei Indexes", "date": "2026-10-05"},
        # Malformed date — must be skipped, not crash.
        {"release_id": 10, "release_name": "Consumer Price Index", "date": "not-a-date"},
    ]
}


def _result(payload) -> ConnectorResult:
    return ConnectorResult(
        connector_name="fred_calendar", fetched_at=datetime.now(timezone.utc), status="ok", raw_payload=payload
    )


def test_keeps_only_watched_release_ids() -> None:
    releases = FredCalendarNormalizer().normalize(_result(FIXTURE))
    assert len(releases) == 2
    assert {r.release_id for r in releases} == {10, 50}


def test_uses_our_own_curated_display_name_not_fred_raw_name() -> None:
    releases = FredCalendarNormalizer().normalize(_result(FIXTURE))
    cpi = next(r for r in releases if r.release_id == 10)
    assert cpi.release_name == "CPI"
    assert cpi.release_date == date(2026, 10, 14)


def test_error_status_returns_empty() -> None:
    result = ConnectorResult(connector_name="fred_calendar", fetched_at=datetime.now(timezone.utc), status="error", error="boom")
    assert FredCalendarNormalizer().normalize(result) == []


def test_empty_payload_returns_empty() -> None:
    assert FredCalendarNormalizer().normalize(_result({})) == []
