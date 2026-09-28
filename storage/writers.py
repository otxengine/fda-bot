"""Idempotent upsert helpers — every ingestion path writes through these
rather than raw INSERTs, so a re-run or overlapping catch-up fetch never
creates duplicate rows (see plan's Data Model note on upserts).

Each function commits its own transaction: this is SQLite in WAL mode (see
storage/db.py), and a committed write is what makes it visible to the
dashboard's concurrent read-only connections in another process."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

from ingestion.models import (
    EarningsEstimate,
    EconomicRelease,
    FdaBotCpBucket,
    FdaBotPerformanceSnapshot,
    FdaBotScoreBucket,
    FdaBotSignal,
    FomcEvent,
    FundamentalsSnapshot,
    MacroPoint,
    NewsArticle,
    PriceBar,
    Quote,
)


def upsert_price_bars(conn: sqlite3.Connection, bars: list[PriceBar]) -> int:
    if not bars:
        return 0
    now = datetime.now(timezone.utc)
    conn.executemany(
        """
        INSERT INTO price_bars (symbol, ts, interval, open, high, low, close, volume, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol, ts, interval) DO UPDATE SET
            open = excluded.open, high = excluded.high, low = excluded.low,
            close = excluded.close, volume = excluded.volume,
            source = excluded.source, fetched_at = excluded.fetched_at
        """,
        [
            (b.symbol, b.ts, b.interval, b.open, b.high, b.low, b.close, b.volume, b.source, now)
            for b in bars
        ],
    )
    conn.commit()
    return len(bars)


def upsert_quotes_latest(conn: sqlite3.Connection, quotes: list[Quote]) -> int:
    if not quotes:
        return 0
    conn.executemany(
        """
        INSERT INTO quotes_latest (symbol, price, change_pct, volume, market_cap, pe_ratio,
            pb_ratio, dividend_yield, fifty_two_wk_high, fifty_two_wk_low, as_of, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol) DO UPDATE SET
            price = excluded.price, change_pct = excluded.change_pct, volume = excluded.volume,
            market_cap = excluded.market_cap, pe_ratio = excluded.pe_ratio, pb_ratio = excluded.pb_ratio,
            dividend_yield = excluded.dividend_yield, fifty_two_wk_high = excluded.fifty_two_wk_high,
            fifty_two_wk_low = excluded.fifty_two_wk_low, as_of = excluded.as_of, source = excluded.source
        """,
        [
            (q.symbol, q.price, q.change_pct, q.volume, q.market_cap, q.pe_ratio, q.pb_ratio,
             q.dividend_yield, q.fifty_two_wk_high, q.fifty_two_wk_low, q.as_of, q.source)
            for q in quotes
        ],
    )
    conn.commit()
    return len(quotes)


def append_fundamentals_history(conn: sqlite3.Connection, snaps: list[FundamentalsSnapshot]) -> int:
    if not snaps:
        return 0
    conn.executemany(
        """
        INSERT INTO fundamentals_history (symbol, as_of, pe_ratio, forward_pe, peg_ratio, eps,
            revenue_ttm, revenue_growth_yoy, profit_margin, debt_to_equity, free_cash_flow, roe, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol, as_of) DO UPDATE SET
            pe_ratio = excluded.pe_ratio, forward_pe = excluded.forward_pe, peg_ratio = excluded.peg_ratio,
            eps = excluded.eps, revenue_ttm = excluded.revenue_ttm,
            revenue_growth_yoy = excluded.revenue_growth_yoy, profit_margin = excluded.profit_margin,
            debt_to_equity = excluded.debt_to_equity, free_cash_flow = excluded.free_cash_flow,
            roe = excluded.roe, source = excluded.source
        """,
        [
            (s.symbol, s.as_of, s.pe_ratio, s.forward_pe, s.peg_ratio, s.eps, s.revenue_ttm,
             s.revenue_growth_yoy, s.profit_margin, s.debt_to_equity, s.free_cash_flow, s.roe, s.source)
            for s in snaps
        ],
    )
    conn.commit()
    return len(snaps)


def append_earnings_estimates(conn: sqlite3.Connection, estimates: list[EarningsEstimate]) -> int:
    if not estimates:
        return 0
    conn.executemany(
        """
        INSERT INTO earnings_estimates (symbol, as_of, period, metric, avg_estimate, growth_yoy,
            number_of_analysts, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol, as_of, period, metric) DO UPDATE SET
            avg_estimate = excluded.avg_estimate, growth_yoy = excluded.growth_yoy,
            number_of_analysts = excluded.number_of_analysts, source = excluded.source
        """,
        [
            (e.symbol, e.as_of, e.period, e.metric, e.avg_estimate, e.growth_yoy,
             e.number_of_analysts, e.source)
            for e in estimates
        ],
    )
    conn.commit()
    return len(estimates)


def upsert_macro_series(conn: sqlite3.Connection, points: list[MacroPoint]) -> int:
    if not points:
        return 0
    now = datetime.now(timezone.utc)
    conn.executemany(
        """
        INSERT INTO macro_series (series_id, date, value, source, fetched_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (series_id, date) DO UPDATE SET
            value = excluded.value, source = excluded.source, fetched_at = excluded.fetched_at
        """,
        [(p.series_id, p.date, p.value, p.source, now) for p in points],
    )
    conn.commit()
    return len(points)


def upsert_news_articles(conn: sqlite3.Connection, articles: list[NewsArticle]) -> int:
    if not articles:
        return 0
    now = datetime.now(timezone.utc)
    conn.executemany(
        """
        INSERT INTO news_articles (article_id, symbol, headline, url, published_at, source,
            sentiment_score, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (article_id) DO NOTHING
        """,
        [
            (a.article_id, a.symbol, a.headline, a.url, a.published_at, a.source, a.sentiment_score, now)
            for a in articles
        ],
    )
    conn.commit()
    return len(articles)


def upsert_fomc_events(conn: sqlite3.Connection, events: list[FomcEvent]) -> int:
    """FOMC meeting dates aren't a numeric series — stored as info-severity
    flags rather than a new table (see plan: reuse existing schema)."""
    if not events:
        return 0
    now = datetime.now(timezone.utc)
    rows = []
    for e in events:
        flag_id = hashlib.sha256(f"fomc:{e.meeting_date.isoformat()}".encode()).hexdigest()
        kind = "2-day" if e.is_two_day else "1-day"
        rows.append(
            (
                flag_id,
                "fomc_meeting",
                None,
                "info",
                f"FOMC meeting ({kind}) starting {e.meeting_date.isoformat()}",
                now,
            )
        )
    conn.executemany(
        """
        INSERT INTO flags (flag_id, flag_type, symbol, severity, message, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (flag_id) DO NOTHING
        """,
        rows,
    )
    conn.commit()
    return len(events)


def upsert_economic_releases(conn: sqlite3.Connection, releases: list[EconomicRelease]) -> int:
    """Scheduled economic data releases (CPI, jobs report, GDP, etc.) — like
    FOMC events, stored as info-severity flags rather than a new table."""
    if not releases:
        return 0
    now = datetime.now(timezone.utc)
    rows = []
    for r in releases:
        flag_id = hashlib.sha256(f"econ_release:{r.release_id}:{r.release_date.isoformat()}".encode()).hexdigest()
        rows.append(
            (
                flag_id,
                "economic_release",
                None,
                "info",
                f"{r.release_name} scheduled for {r.release_date.isoformat()}",
                now,
            )
        )
    conn.executemany(
        """
        INSERT INTO flags (flag_id, flag_type, symbol, severity, message, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (flag_id) DO NOTHING
        """,
        rows,
    )
    conn.commit()
    return len(releases)


def append_fda_bot_performance_snapshots(conn: sqlite3.Connection, snaps: list[FdaBotPerformanceSnapshot]) -> int:
    if not snaps:
        return 0
    conn.executemany(
        """
        INSERT INTO fda_bot_performance_snapshots (fetched_at, total_alerts_tracked, n_with_1d,
            win_rate_1d, win_rate_3d, avg_return_1d, avg_return_3d,
            n_to_target, win_rate_to_target, avg_return_to_target,
            upcoming_events, events_next_7d, total_signals, historical_records, last_scan_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (fetched_at) DO NOTHING
        """,
        [
            (s.fetched_at, s.total_alerts_tracked, s.n_with_1d, s.win_rate_1d, s.win_rate_3d,
             s.avg_return_1d, s.avg_return_3d, s.n_to_target, s.win_rate_to_target,
             s.avg_return_to_target, s.upcoming_events, s.events_next_7d,
             s.total_signals, s.historical_records, s.last_scan_at)
            for s in snaps
        ],
    )
    conn.commit()
    return len(snaps)


def append_fda_bot_cp_buckets(conn: sqlite3.Connection, buckets: list[FdaBotCpBucket]) -> int:
    if not buckets:
        return 0
    conn.executemany(
        """
        INSERT INTO fda_bot_cp_buckets (fetched_at, bucket, n, win_rate, avg_change)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (fetched_at, bucket) DO NOTHING
        """,
        [(b.fetched_at, b.bucket, b.n, b.win_rate, b.avg_change) for b in buckets],
    )
    conn.commit()
    return len(buckets)


def append_fda_bot_score_buckets(conn: sqlite3.Connection, buckets: list[FdaBotScoreBucket]) -> int:
    if not buckets:
        return 0
    conn.executemany(
        """
        INSERT INTO fda_bot_score_buckets (fetched_at, range, n, p_up5, p_up10, p_down5,
            p_down10, avg_change, median_change)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (fetched_at, range) DO NOTHING
        """,
        [
            (b.fetched_at, b.range, b.n, b.p_up5, b.p_up10, b.p_down5, b.p_down10,
             b.avg_change, b.median_change)
            for b in buckets
        ],
    )
    conn.commit()
    return len(buckets)


def replace_fda_bot_signals(conn: sqlite3.Connection, signals: list[FdaBotSignal]) -> int:
    """Full replace, not upsert: fda_bot_signals mirrors "what's currently in
    the bot's 0-7 day actionable window" — a ticker whose event just passed
    must disappear from here on the next fetch, not linger with stale data."""
    conn.execute("DELETE FROM fda_bot_signals")
    if not signals:
        conn.commit()
        return 0
    conn.executemany(
        """
        INSERT INTO fda_bot_signals (ticker, company, event_type, event_date, days_until,
            stock_signal, stock_signal_reason, entry_price, stop_loss_price, target_date,
            composite_score, expected_move_pct, entry_window, call_put_ratio, iv_rank,
            premium_flow, liquidity_warning, iv_crush_warning, sector_momentum, macro_risk_flag,
            fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (s.ticker, s.company, s.event_type, s.event_date, s.days_until, s.stock_signal,
             s.stock_signal_reason, s.entry_price, s.stop_loss_price, s.target_date,
             s.composite_score, s.expected_move_pct, s.entry_window, s.call_put_ratio,
             s.iv_rank, s.premium_flow, s.liquidity_warning, s.iv_crush_warning,
             s.sector_momentum, s.macro_risk_flag, s.fetched_at)
            for s in signals
        ],
    )
    conn.commit()
    return len(signals)


def create_conversation(conn: sqlite3.Connection, conversation_id: str, title: str) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO chat_conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (conversation_id, title, now, now),
    )
    conn.commit()


def append_chat_message(conn: sqlite3.Connection, conversation_id: str, role: str, content: str) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO chat_history (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conversation_id, role, content, now),
    )
    # Bumps updated_at so the sidebar's most-recently-active conversation sorts first.
    conn.execute("UPDATE chat_conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
    conn.commit()


def delete_conversation(conn: sqlite3.Connection, conversation_id: str) -> None:
    conn.execute("DELETE FROM chat_history WHERE conversation_id = ?", (conversation_id,))
    conn.execute("DELETE FROM chat_conversations WHERE id = ?", (conversation_id,))
    conn.commit()


def log_connector_run(
    conn: sqlite3.Connection,
    connector_name: str,
    status: str,
    rows_ingested: int = 0,
    error_message: str | None = None,
    duration_ms: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO connector_runs (connector_name, run_ts, status, rows_ingested, error_message, duration_ms)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (connector_name, run_ts) DO NOTHING
        """,
        (connector_name, datetime.now(timezone.utc), status, rows_ingested, error_message, duration_ms),
    )
    conn.commit()
