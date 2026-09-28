"""Normalizes connectors/fda_bot.py's raw multi-endpoint payload into
canonical records. See that module's docstring for what this integration is
and isn't (read-only, one-way, from the user's own separate fda-bot
service)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from connectors.base import BaseNormalizer, ConnectorResult
from ingestion.models import (
    FdaBotCpBucket,
    FdaBotPerformanceSnapshot,
    FdaBotScoreBucket,
    FdaBotSignal,
)


class FdaBotNormalizer(BaseNormalizer):
    def normalize(self, result: ConnectorResult) -> list[object]:
        if result.status != "ok" or not result.raw_payload:
            return []
        payload = result.raw_payload
        now = datetime.now(timezone.utc)
        records: list[object] = []

        status = payload.get("status") or {}
        perf = payload.get("performance") or {}
        overall = perf.get("overall") or {}

        # perf has real content once the bot's AlertOutcome table has rows —
        # "message"-only responses (see its /api/performance: "No outcome
        # data yet") carry no total_alerts_tracked key, so skip those cleanly
        # rather than writing a snapshot of zeros.
        if "total_alerts_tracked" in perf:
            records.append(
                FdaBotPerformanceSnapshot(
                    fetched_at=now,
                    total_alerts_tracked=perf.get("total_alerts_tracked") or 0,
                    n_with_1d=overall.get("n") or 0,
                    win_rate_1d=overall.get("hit_rate_1d_pct"),
                    win_rate_3d=overall.get("hit_rate_3d_pct"),
                    avg_return_1d=overall.get("avg_return_1d"),
                    avg_return_3d=overall.get("avg_return_3d"),
                    n_to_target=overall.get("n_to_target") or 0,
                    win_rate_to_target=overall.get("win_rate_to_target"),
                    avg_return_to_target=overall.get("avg_return_to_target"),
                    upcoming_events=status.get("upcoming_events") or 0,
                    events_next_7d=status.get("events_next_7d") or 0,
                    total_signals=status.get("total_signals") or 0,
                    historical_records=status.get("historical_records") or 0,
                    last_scan_at=_parse_dt(status.get("last_scan")),
                )
            )

        for b in perf.get("cp_buckets") or []:
            if not b.get("n"):
                continue
            records.append(
                FdaBotCpBucket(
                    fetched_at=now, bucket=b["bucket"], n=b["n"],
                    win_rate=b.get("win_rate"), avg_change=b.get("avg_change"),
                )
            )

        calib = payload.get("calibration") or {}
        for b in calib.get("buckets") or []:
            if not b.get("n"):
                continue
            records.append(
                FdaBotScoreBucket(
                    fetched_at=now, range=b["range"], n=b["n"],
                    p_up5=b.get("p_up5"), p_up10=b.get("p_up10"),
                    p_down5=b.get("p_down5"), p_down10=b.get("p_down10"),
                    avg_change=b.get("avg_change"), median_change=b.get("median_change"),
                )
            )

        sig_payload = payload.get("stock_signals") or {}
        for s in sig_payload.get("signals") or []:
            if not s.get("ticker"):
                continue
            records.append(
                FdaBotSignal(
                    ticker=s["ticker"],
                    company=s.get("company"),
                    event_type=s.get("event_type"),
                    event_date=_parse_date(s.get("event_date")),
                    days_until=s.get("days_until"),
                    stock_signal=s.get("stock_signal"),
                    stock_signal_reason=s.get("stock_signal_reason"),
                    entry_price=s.get("entry_price"),
                    stop_loss_price=s.get("stop_loss_price"),
                    target_date=s.get("target_date"),
                    composite_score=s.get("composite_score"),
                    expected_move_pct=s.get("expected_move_pct"),
                    entry_window=s.get("entry_window"),
                    call_put_ratio=s.get("call_put_ratio"),
                    iv_rank=s.get("iv_rank"),
                    premium_flow=s.get("premium_flow"),
                    liquidity_warning=bool(s.get("liquidity_warning")),
                    iv_crush_warning=bool(s.get("iv_crush_warning")),
                    sector_momentum=s.get("sector_momentum"),
                    macro_risk_flag=s.get("macro_risk_flag"),
                    fetched_at=now,
                )
            )

        return records


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None
