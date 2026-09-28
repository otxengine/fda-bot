"""DB schema smoke test (plan's Verification section): build a fresh DB from
schema.sql, insert a sample row into every table, confirm round-trip reads —
catches schema drift early."""

from __future__ import annotations

import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from storage.db import write_connection


def test_fresh_schema_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "schema_check.db")
        now = datetime.now(timezone.utc)
        today = date.today()

        conn.execute(
            "INSERT INTO instruments (symbol, name, asset_type, added_at) VALUES (?, ?, ?, ?)",
            ("AAPL", "Apple Inc.", "equity", now),
        )
        conn.execute(
            "INSERT INTO price_bars (symbol, ts, interval, open, high, low, close, volume, source, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("AAPL", now, "1d", 1, 2, 0.5, 1.5, 1000, "test", now),
        )
        conn.execute(
            "INSERT INTO quotes_latest (symbol, price, as_of, source) VALUES (?, ?, ?, ?)",
            ("AAPL", 150.0, now, "test"),
        )
        conn.execute(
            "INSERT INTO fundamentals_history (symbol, as_of, source) VALUES (?, ?, ?)",
            ("AAPL", now, "test"),
        )
        conn.execute(
            "INSERT INTO macro_series (series_id, date, value, source, fetched_at) VALUES (?, ?, ?, ?, ?)",
            ("FRED:TEST", today, 1.23, "test", now),
        )
        conn.execute(
            "INSERT INTO macro_series_meta (series_id, display_name) VALUES (?, ?)",
            ("FRED:TEST", "Test Series"),
        )
        conn.execute(
            "INSERT INTO screener_snapshots (run_id, run_ts, symbol, rank, score, criteria_json) VALUES (?, ?, ?, ?, ?, ?)",
            ("run1", now, "AAPL", 1, 99.9, "{}"),
        )
        conn.execute(
            "INSERT INTO watchlist (symbol, added_at) VALUES (?, ?)", ("AAPL", now)
        )
        conn.execute(
            "INSERT INTO news_articles (article_id, headline, url, published_at, source) VALUES (?, ?, ?, ?, ?)",
            ("id1", "Test headline", "https://example.com", now, "test"),
        )
        conn.execute(
            "INSERT INTO flags (flag_id, flag_type, severity, message, created_at) VALUES (?, ?, ?, ?, ?)",
            ("flag1", "test_flag", "info", "test message", now),
        )
        conn.execute(
            "INSERT INTO macro_regime_history (computed_at, regime_label, rates_score, inflation_score, "
            "employment_score, growth_score, details_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now, "Expansion", 1.0, 1.0, 1.0, 1.0, "{}"),
        )
        conn.execute(
            "INSERT INTO connector_runs (connector_name, run_ts, status) VALUES (?, ?, ?)",
            ("test_connector", now, "ok"),
        )
        conn.commit()

        for table in [
            "instruments", "price_bars", "quotes_latest", "fundamentals_history", "macro_series",
            "macro_series_meta", "screener_snapshots", "watchlist", "news_articles", "flags",
            "macro_regime_history", "connector_runs",
        ]:
            count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            assert count == 1, f"expected 1 row in {table}, got {count}"

        conn.close()
