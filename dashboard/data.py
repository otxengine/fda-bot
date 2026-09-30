"""Shared, cached read layer for every dashboard page. Every read opens a
short-lived connection via storage.db.read_connection() and closes it before
returning — see plan's ADR-2/ADR-3 (the dashboard never holds a DB connection
open across a Streamlit rerun).

A few functions here DO write (watchlist add/remove/notes, promoting a
screener row). Those are rare, user-initiated, one-row writes — they open a
short-lived write connection and, because SQLite's WAL mode (see storage/db.py)
handles a writer and readers concurrently across processes, this is safe
even while the scheduler is mid-tick. A `busy_timeout` (set in storage/db.py)
means a momentary conflict retries briefly rather than failing outright.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from analysis import macro_regime, screener as screener_module, sector_rotation
from analysis.technicals import get_price_history, with_technicals
from storage.db import read_connection

CACHE_TTL = 120  # seconds — matches the plan's "feels live without hammering the DB" intent


@st.cache_data(ttl=CACHE_TTL)
def load_latest_regime() -> dict | None:
    with read_connection() as conn:
        return macro_regime.get_latest_regime(conn)


@st.cache_data(ttl=CACHE_TTL)
def load_regime_history(limit: int = 200) -> pd.DataFrame:
    with read_connection() as conn:
        return macro_regime.get_regime_history(conn, limit)


@st.cache_data(ttl=CACHE_TTL)
def load_indicator_series(series_id: str) -> pd.Series:
    with read_connection() as conn:
        return macro_regime.get_series(conn, series_id)


@st.cache_data(ttl=CACHE_TTL)
def load_sector_rotation(lookback_days: int = 21) -> pd.DataFrame:
    with read_connection() as conn:
        return sector_rotation.compute_sector_rotation(conn, lookback_days=lookback_days)


@st.cache_data(ttl=CACHE_TTL)
def load_additional_indicators() -> pd.DataFrame:
    from analysis.additional_indicators import get_additional_indicators_snapshot

    with read_connection() as conn:
        rows = get_additional_indicators_snapshot(conn)
    return pd.DataFrame(rows)


@st.cache_data(ttl=CACHE_TTL)
def load_quant_table() -> pd.DataFrame:
    """Backs the Idea Generation page (sector+quant screener, following the
    user's own Excel/Zacks guide's methodology) — one row per S&P 500 symbol
    with every derived metric (P/E, P/S, SG1, EG1, EG2, PEG). See
    analysis/sector_quant_screener.py."""
    from analysis.sector_quant_screener import build_quant_table

    with read_connection() as conn:
        return build_quant_table(conn)


@st.cache_data(ttl=CACHE_TTL)
def load_watchlist_symbols() -> list[str]:
    with read_connection() as conn:
        return [r[0] for r in conn.execute("SELECT symbol FROM watchlist ORDER BY symbol").fetchall()]


@st.cache_data(ttl=CACHE_TTL)
def load_watchlist_details() -> pd.DataFrame:
    with read_connection() as conn:
        rows = conn.execute(
            "SELECT w.symbol, w.notes, w.target_price, w.tags, q.price, q.change_pct, "
            "q.pe_ratio, q.market_cap, q.fifty_two_wk_high, q.fifty_two_wk_low "
            "FROM watchlist w LEFT JOIN quotes_latest q ON q.symbol = w.symbol ORDER BY w.symbol"
        ).fetchall()
    cols = ["symbol", "notes", "target_price", "tags", "price", "change_pct",
            "pe_ratio", "market_cap", "fifty_two_wk_high", "fifty_two_wk_low"]
    return pd.DataFrame(rows, columns=cols)


@st.cache_data(ttl=CACHE_TTL)
def load_price_history_with_technicals(symbol: str, interval: str = "1d") -> pd.DataFrame:
    with read_connection() as conn:
        df = get_price_history(conn, symbol, interval)
    return with_technicals(df)


@st.cache_data(ttl=CACHE_TTL)
def load_fundamentals_history(symbol: str, limit: int = 8) -> pd.DataFrame:
    with read_connection() as conn:
        rows = conn.execute(
            "SELECT as_of, pe_ratio, forward_pe, eps, revenue_growth_yoy, profit_margin, roe "
            "FROM fundamentals_history WHERE symbol = ? ORDER BY as_of DESC LIMIT ?",
            [symbol, limit],
        ).fetchall()
    cols = ["as_of", "pe_ratio", "forward_pe", "eps", "revenue_growth_yoy", "profit_margin", "roe"]
    return pd.DataFrame(rows, columns=cols).iloc[::-1].reset_index(drop=True)


@st.cache_data(ttl=CACHE_TTL)
def load_earnings_estimates(symbol: str) -> pd.DataFrame:
    """Latest analyst consensus per period/metric — '0y'/'+1y' rows are what
    answer "current year vs next year revenue/earnings and growth rate"."""
    with read_connection() as conn:
        rows = conn.execute(
            """
            SELECT metric, period, avg_estimate, growth_yoy, number_of_analysts, as_of
            FROM earnings_estimates
            WHERE symbol = ? AND as_of = (SELECT max(as_of) FROM earnings_estimates WHERE symbol = ?)
            ORDER BY metric, period
            """,
            [symbol, symbol],
        ).fetchall()
    cols = ["metric", "period", "avg_estimate", "growth_yoy", "number_of_analysts", "as_of"]
    return pd.DataFrame(rows, columns=cols)


@st.cache_data(ttl=CACHE_TTL)
def load_news(symbol: str | None = None, limit: int = 30) -> pd.DataFrame:
    with read_connection() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT headline, url, published_at, source, sentiment_score, symbol "
                "FROM news_articles WHERE symbol = ? ORDER BY published_at DESC LIMIT ?",
                [symbol, limit],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT headline, url, published_at, source, sentiment_score, symbol "
                "FROM news_articles ORDER BY published_at DESC LIMIT ?",
                [limit],
            ).fetchall()
    cols = ["headline", "url", "published_at", "source", "sentiment_score", "symbol"]
    return pd.DataFrame(rows, columns=cols)


@st.cache_data(ttl=CACHE_TTL)
def load_flags(limit: int = 50) -> pd.DataFrame:
    with read_connection() as conn:
        rows = conn.execute(
            "SELECT flag_type, symbol, severity, message, created_at FROM flags "
            "ORDER BY created_at DESC LIMIT ?",
            [limit],
        ).fetchall()
    cols = ["flag_type", "symbol", "severity", "message", "created_at"]
    return pd.DataFrame(rows, columns=cols)


@st.cache_data(ttl=CACHE_TTL)
def load_connector_health() -> pd.DataFrame:
    """One row per connector: its most recent run's status/timestamp and a
    rolling failure-streak count — the "Connector Health" panel from the
    plan's Verification section, so silent scraper breakage stays visible."""
    with read_connection() as conn:
        rows = conn.execute(
            """
            SELECT connector_name, status, run_ts, error_message, duration_ms
            FROM connector_runs
            ORDER BY run_ts DESC
            """
        ).fetchall()
    df = pd.DataFrame(rows, columns=["connector_name", "status", "run_ts", "error_message", "duration_ms"])
    if df.empty:
        return df
    latest = df.sort_values("run_ts").groupby("connector_name").tail(1).reset_index(drop=True)

    def _failure_streak(name: str) -> int:
        history = df[df["connector_name"] == name].sort_values("run_ts", ascending=False)
        streak = 0
        for status in history["status"]:
            if status == "error":
                streak += 1
            else:
                break
        return streak

    latest["failure_streak"] = latest["connector_name"].apply(_failure_streak)
    return latest.sort_values("connector_name").reset_index(drop=True)


@st.cache_data(ttl=CACHE_TTL)
def load_screener_latest(n: int = 50) -> pd.DataFrame:
    with read_connection() as conn:
        latest_run = conn.execute(
            "SELECT run_id, run_ts FROM screener_snapshots ORDER BY run_ts DESC LIMIT 1"
        ).fetchone()
        if latest_run is None:
            return pd.DataFrame()
        run_id, run_ts = latest_run
        rows = conn.execute(
            "SELECT symbol, rank, score FROM screener_snapshots WHERE run_id = ? ORDER BY rank LIMIT ?",
            [run_id, n],
        ).fetchall()
    df = pd.DataFrame(rows, columns=["symbol", "rank", "score"])
    df.attrs["run_ts"] = run_ts
    return df


# --- Small, rare, user-initiated writes -------------------------------------

def _short_write_connection() -> sqlite3.Connection:
    from storage.db import write_connection

    return write_connection()


def add_to_watchlist(symbol: str, notes: str = "") -> tuple[bool, str]:
    try:
        conn = _short_write_connection()
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO watchlist (symbol, notes, added_at) VALUES (?, ?, ?) "
            "ON CONFLICT (symbol) DO UPDATE SET notes = excluded.notes",
            (symbol.upper(), notes, now),
        )
        conn.commit()
        conn.close()
        load_watchlist_symbols.clear()
        load_watchlist_details.clear()
        return True, f"{symbol.upper()} נוסף לרשימת המעקב"
    except sqlite3.OperationalError as exc:
        return False, f"לא ניתן היה לשמור כרגע (ייתכן שהתזמון באמצע כתיבה) — נסו שוב: {exc}"


def remove_from_watchlist(symbol: str) -> tuple[bool, str]:
    try:
        conn = _short_write_connection()
        conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
        conn.commit()
        conn.close()
        load_watchlist_symbols.clear()
        load_watchlist_details.clear()
        return True, f"{symbol} הוסר"
    except sqlite3.OperationalError as exc:
        return False, f"לא ניתן היה לשמור כרגע (ייתכן שהתזמון באמצע כתיבה) — נסו שוב: {exc}"


# --- Chat conversations (persisted, multiple, resumable — see storage/schema.sql) ---
# Not cached (unlike the rest of this module): the chat page needs the
# freshest state right after every append/new/delete, and these tables are
# tiny and read rarely (sidebar render + one load per switch), so a cache
# buys nothing here and would just risk showing stale history.

def list_conversations(limit: int = 50) -> list[dict[str, str]]:
    with read_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, updated_at FROM chat_conversations ORDER BY updated_at DESC LIMIT ?",
            [limit],
        ).fetchall()
    return [{"id": r[0], "title": r[1], "updated_at": r[2]} for r in rows]


def load_conversation_messages(conversation_id: str) -> list[dict[str, str]]:
    with read_connection() as conn:
        rows = conn.execute(
            "SELECT role, content FROM chat_history WHERE conversation_id = ? ORDER BY id ASC",
            [conversation_id],
        ).fetchall()
    return [{"role": role, "content": content} for role, content in rows]


def start_new_conversation(first_message: str) -> str:
    """Lazily creates the conversation row on the FIRST message — a "New
    conversation" click alone doesn't write anything, matching normal AI
    chat apps where an empty draft isn't a saved thread yet."""
    import uuid

    from storage.writers import create_conversation

    conversation_id = str(uuid.uuid4())
    stripped = first_message.strip()
    title = stripped[:60] + ("…" if len(stripped) > 60 else "")
    conn = _short_write_connection()
    create_conversation(conn, conversation_id, title or "New conversation")
    conn.close()
    return conversation_id


def save_chat_message(conversation_id: str, role: str, content: str) -> None:
    from storage.writers import append_chat_message

    conn = _short_write_connection()
    append_chat_message(conn, conversation_id, role, content)
    conn.close()


def delete_conversation(conversation_id: str) -> None:
    from storage.writers import delete_conversation as _delete

    conn = _short_write_connection()
    _delete(conn, conversation_id)
    conn.close()


# --- FDA-Bot integration (see connectors/fda_bot.py) ------------------------

@st.cache_data(ttl=CACHE_TTL)
def load_fda_bot_performance_history(limit: int = 500) -> pd.DataFrame:
    """Oldest-first, for trend charts."""
    with read_connection() as conn:
        rows = conn.execute(
            "SELECT fetched_at, total_alerts_tracked, n_with_1d, win_rate_1d, win_rate_3d, "
            "avg_return_1d, avg_return_3d, n_to_target, win_rate_to_target, avg_return_to_target, "
            "upcoming_events, events_next_7d, total_signals, historical_records, last_scan_at "
            "FROM fda_bot_performance_snapshots ORDER BY fetched_at DESC LIMIT ?",
            [limit],
        ).fetchall()
    cols = [
        "fetched_at", "total_alerts_tracked", "n_with_1d", "win_rate_1d", "win_rate_3d",
        "avg_return_1d", "avg_return_3d", "n_to_target", "win_rate_to_target", "avg_return_to_target",
        "upcoming_events", "events_next_7d", "total_signals", "historical_records", "last_scan_at",
    ]
    df = pd.DataFrame(rows, columns=cols)
    return df.iloc[::-1].reset_index(drop=True)


@st.cache_data(ttl=CACHE_TTL)
def load_fda_bot_cp_buckets() -> pd.DataFrame:
    """Latest fetch's call/put-ratio win-rate breakdown, in bucket order."""
    order = ["<1.0", "1.0-2.0", "2.0-3.5", "3.5-5.0", "5.0-8.0", ">8.0"]
    with read_connection() as conn:
        latest = conn.execute("SELECT MAX(fetched_at) FROM fda_bot_cp_buckets").fetchone()[0]
        if not latest:
            return pd.DataFrame()
        rows = conn.execute(
            "SELECT bucket, n, win_rate, avg_change FROM fda_bot_cp_buckets WHERE fetched_at = ?",
            [latest],
        ).fetchall()
    df = pd.DataFrame(rows, columns=["bucket", "n", "win_rate", "avg_change"])
    df["bucket"] = pd.Categorical(df["bucket"], categories=order, ordered=True)
    return df.sort_values("bucket").reset_index(drop=True)


@st.cache_data(ttl=CACHE_TTL)
def load_fda_bot_score_buckets() -> pd.DataFrame:
    """Latest fetch's composite-score win-rate breakdown, low to high."""
    order = ["0-35", "35-50", "50-65", "65-80", "80-100"]
    with read_connection() as conn:
        latest = conn.execute("SELECT MAX(fetched_at) FROM fda_bot_score_buckets").fetchone()[0]
        if not latest:
            return pd.DataFrame()
        rows = conn.execute(
            "SELECT range, n, p_up5, p_up10, p_down5, p_down10, avg_change, median_change "
            "FROM fda_bot_score_buckets WHERE fetched_at = ?",
            [latest],
        ).fetchall()
    cols = ["range", "n", "p_up5", "p_up10", "p_down5", "p_down10", "avg_change", "median_change"]
    df = pd.DataFrame(rows, columns=cols)
    df["range"] = pd.Categorical(df["range"], categories=order, ordered=True)
    return df.sort_values("range").reset_index(drop=True)


@st.cache_data(ttl=CACHE_TTL)
def load_fda_bot_signals() -> pd.DataFrame:
    """Current 0-7 day actionable tickers, BUY/EARLY_BUY first."""
    with read_connection() as conn:
        rows = conn.execute(
            "SELECT ticker, company, event_type, event_date, days_until, stock_signal, "
            "stock_signal_reason, entry_price, stop_loss_price, target_date, composite_score, "
            "expected_move_pct, entry_window, call_put_ratio, iv_rank, premium_flow, "
            "liquidity_warning, iv_crush_warning, sector_momentum, macro_risk_flag, fetched_at "
            "FROM fda_bot_signals ORDER BY "
            "CASE stock_signal WHEN 'BUY' THEN 0 WHEN 'EARLY_BUY' THEN 1 "
            "WHEN 'WATCH' THEN 2 ELSE 3 END, composite_score DESC"
        ).fetchall()
    cols = [
        "ticker", "company", "event_type", "event_date", "days_until", "stock_signal",
        "stock_signal_reason", "entry_price", "stop_loss_price", "target_date", "composite_score",
        "expected_move_pct", "entry_window", "call_put_ratio", "iv_rank", "premium_flow",
        "liquidity_warning", "iv_crush_warning", "sector_momentum", "macro_risk_flag", "fetched_at",
    ]
    return pd.DataFrame(rows, columns=cols)
