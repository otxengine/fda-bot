"""BLS normalizer against a recorded (hand-built, no live network) fixture
shape — including a malformed/partial-response edge case (plan's Verification
section)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from connectors.base import ConnectorResult
from ingestion.normalizers.bls import BlsNormalizer

FIXTURE = {
    "status": "REQUEST_SUCCEEDED",
    "Results": {
        "series": [
            {
                "seriesID": "LNS14000000",
                "data": [
                    {"year": "2024", "period": "M02", "periodName": "February", "value": "3.9"},
                    {"year": "2024", "period": "M01", "periodName": "January", "value": "3.7"},
                    # Malformed rows a real response can contain — must be skipped, not crash:
                    {"year": "2024", "period": "M13", "periodName": "Annual", "value": "3.8"},
                    {"year": "2024", "period": "M03", "periodName": "March", "value": "not_a_number"},
                ],
            }
        ]
    },
}


def _result(payload) -> ConnectorResult:
    return ConnectorResult(
        connector_name="bls", fetched_at=datetime.now(timezone.utc), status="ok", raw_payload=payload
    )


def test_normalizes_monthly_points() -> None:
    points = BlsNormalizer().normalize(_result(FIXTURE))
    by_date = {p.date: p.value for p in points}
    assert by_date[date(2024, 2, 1)] == 3.9
    assert by_date[date(2024, 1, 1)] == 3.7
    assert all(p.series_id == "BLS:LNS14000000" for p in points)


def test_skips_annual_and_malformed_rows() -> None:
    points = BlsNormalizer().normalize(_result(FIXTURE))
    assert len(points) == 2  # the M13 and non-numeric rows must be dropped, not raise


def test_error_status_returns_empty() -> None:
    result = ConnectorResult(
        connector_name="bls", fetched_at=datetime.now(timezone.utc), status="error", error="boom"
    )
    assert BlsNormalizer().normalize(result) == []


def test_empty_payload_returns_empty() -> None:
    assert BlsNormalizer().normalize(_result({})) == []
