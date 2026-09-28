"""Unit tests for the fda-bot integration normalizer — see
connectors/fda_bot.py's docstring for what this is (a read-only pull from
the user's own separate FDA/biopharma catalyst scanner service)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from connectors.base import ConnectorResult
from ingestion.models import (
    FdaBotCpBucket,
    FdaBotPerformanceSnapshot,
    FdaBotScoreBucket,
    FdaBotSignal,
)
from ingestion.normalizers.fda_bot import FdaBotNormalizer

FIXTURE = {
    "status": {
        "upcoming_events": 733, "events_next_7d": 29, "total_signals": 5036,
        "last_scan": "2026-09-23T16:25:02.480783", "historical_records": 890,
    },
    "performance": {
        "total_alerts_tracked": 2733,
        "overall": {"n": 1399, "hit_rate_1d_pct": 13.5, "hit_rate_3d_pct": 17.9,
                     "avg_return_1d": 0.03, "avg_return_3d": -0.32},
        "cp_buckets": [
            {"bucket": "<1.0", "n": 54, "win_rate": 15, "avg_change": -0.8},
            {"bucket": ">8.0", "n": 0},  # zero-n bucket — must be skipped, not crash on missing keys
        ],
    },
    "calibration": {
        "buckets": [
            {"range": "65-80", "n": 18, "p_up5": 0.167, "p_up10": 0.056, "p_down5": 0.167,
             "p_down10": 0.056, "avg_change": 1.32, "median_change": -1.33},
            {"range": "80-100", "n": 0},
        ]
    },
    "stock_signals": {
        "signals": [
            {"ticker": "ABCL", "company": "AbCellera", "event_type": "PDUFA", "event_date": "2026-10-01",
             "days_until": 3, "stock_signal": "BUY", "stock_signal_reason": "test",
             "entry_price": 5.1, "stop_loss_price": 4.8, "target_date": "2026-09-30",
             "composite_score": 55.0, "expected_move_pct": 10.0, "entry_window": "optimal",
             "call_put_ratio": 2.5, "iv_rank": 60, "premium_flow": 100000,
             "liquidity_warning": False, "iv_crush_warning": True},
            {"company": "No ticker — must be skipped"},  # missing ticker
        ]
    },
}


def _result(payload) -> ConnectorResult:
    return ConnectorResult(
        connector_name="fda_bot", fetched_at=datetime.now(timezone.utc), status="ok", raw_payload=payload
    )


def test_produces_one_performance_snapshot() -> None:
    records = FdaBotNormalizer().normalize(_result(FIXTURE))
    snaps = [r for r in records if isinstance(r, FdaBotPerformanceSnapshot)]
    assert len(snaps) == 1
    s = snaps[0]
    assert s.total_alerts_tracked == 2733
    assert s.win_rate_1d == 13.5
    assert s.upcoming_events == 733
    assert s.last_scan_at == datetime(2026, 9, 23, 16, 25, 2, 480783)


def test_skips_zero_n_buckets() -> None:
    records = FdaBotNormalizer().normalize(_result(FIXTURE))
    cp = [r for r in records if isinstance(r, FdaBotCpBucket)]
    score = [r for r in records if isinstance(r, FdaBotScoreBucket)]
    assert len(cp) == 1 and cp[0].bucket == "<1.0"
    assert len(score) == 1 and score[0].range == "65-80"


def test_skips_signal_with_no_ticker() -> None:
    records = FdaBotNormalizer().normalize(_result(FIXTURE))
    signals = [r for r in records if isinstance(r, FdaBotSignal)]
    assert len(signals) == 1
    sig = signals[0]
    assert sig.ticker == "ABCL"
    assert sig.event_date == date(2026, 10, 1)
    assert sig.iv_crush_warning is True
    assert sig.liquidity_warning is False


def test_missing_performance_data_produces_no_snapshot() -> None:
    """The bot's own /api/performance returns {"message": "No outcome data
    yet", ...} with no total_alerts_tracked key before its AlertOutcome
    table has any rows — must skip cleanly, not write a snapshot of zeros
    that would look like real (bad) data."""
    payload = {**FIXTURE, "performance": {"message": "No outcome data yet.", "total": 0}}
    records = FdaBotNormalizer().normalize(_result(payload))
    assert not any(isinstance(r, FdaBotPerformanceSnapshot) for r in records)


def test_error_status_returns_empty() -> None:
    result = ConnectorResult(connector_name="fda_bot", fetched_at=datetime.now(timezone.utc), status="error", error="boom")
    assert FdaBotNormalizer().normalize(result) == []


def test_empty_payload_returns_empty() -> None:
    assert FdaBotNormalizer().normalize(_result({})) == []
