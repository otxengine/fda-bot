"""Defense-in-depth regression test: even if price_bars somehow ends up with
mixed UTC offsets in its stored `ts` strings (e.g. old data written before
the yahoo_finance normalizer fix, or a future connector that isn't careful),
get_price_history() must not crash — see
tests/test_connectors/test_yahoo_finance_normalizer.py for the source-side
fix this backstops."""

from __future__ import annotations

import tempfile
from pathlib import Path

from analysis.technicals import get_price_history, with_technicals
from storage.db import write_connection


def test_get_price_history_survives_mixed_utc_offsets_in_stored_data() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "t.db")
        # Insert raw ISO strings with different offsets directly — bypasses
        # the normalizer entirely, simulating already-inconsistent data.
        conn.execute(
            "INSERT INTO price_bars (symbol, ts, interval, open, high, low, close, volume, source, fetched_at) "
            "VALUES ('AAPL', '2024-01-15T09:30:00-05:00', '1d', 100, 101, 99, 100.5, 1000, 'test', '2024-01-15T09:30:00-05:00')"
        )
        conn.execute(
            "INSERT INTO price_bars (symbol, ts, interval, open, high, low, close, volume, source, fetched_at) "
            "VALUES ('AAPL', '2024-07-15T09:30:00-04:00', '1d', 110, 111, 109, 110.5, 2000, 'test', '2024-07-15T09:30:00-04:00')"
        )
        conn.commit()

        df = get_price_history(conn, "AAPL", interval="1d")
        conn.close()

    assert len(df) == 2
    assert df.index.tz is not None


def test_duplicate_index_after_utc_parsing_does_not_crash_indicators() -> None:
    """Regression test for a real crash found live in the background
    service: two DIFFERENT raw ts strings representing the SAME instant
    ("2024-09-25 00:00:00-04:00" exchange-local vs "2024-09-25 04:00:00+00:00"
    already-UTC — leftover stale rows from before the normalizer started
    UTC-normalizing consistently, which never upsert-merged with the new
    rows since the SQL PRIMARY KEY is the raw string) produced duplicate
    index entries that crashed pandas_ta's macd() with "cannot reindex on an
    axis with duplicate labels". get_price_history() must dedupe them before
    anything downstream (with_technicals) ever sees the frame."""
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "t.db")
        # Same real-world instant, two different stored string forms — plus
        # enough more (distinct) days for pandas_ta's indicators to compute.
        conn.execute(
            "INSERT INTO price_bars (symbol, ts, interval, open, high, low, close, volume, source, fetched_at) "
            "VALUES ('AAPL', '2024-09-25 00:00:00-04:00', '1d', 100, 101, 99, 100.5, 1000, 'test', '2024-09-25 00:00:00-04:00')"
        )
        conn.execute(
            "INSERT INTO price_bars (symbol, ts, interval, open, high, low, close, volume, source, fetched_at) "
            "VALUES ('AAPL', '2024-09-25 04:00:00+00:00', '1d', 105, 106, 104, 105.5, 1500, 'test', '2024-09-25 04:00:00+00:00')"
        )
        for i in range(26, 56):
            conn.execute(
                "INSERT INTO price_bars (symbol, ts, interval, open, high, low, close, volume, source, fetched_at) "
                f"VALUES ('AAPL', '2024-09-{i:02d} 04:00:00+00:00', '1d', 100, 101, 99, 100.5, 1000, 'test', '2024-09-{i:02d} 04:00:00+00:00')"
                if i <= 30 else
                "INSERT INTO price_bars (symbol, ts, interval, open, high, low, close, volume, source, fetched_at) "
                f"VALUES ('AAPL', '2024-10-{i-30:02d} 04:00:00+00:00', '1d', 100, 101, 99, 100.5, 1000, 'test', '2024-10-{i-30:02d} 04:00:00+00:00')"
            )
        conn.commit()

        df = get_price_history(conn, "AAPL", interval="1d")
        assert not df.index.duplicated().any(), "get_price_history() must return a de-duplicated index"

        result = with_technicals(df)  # this line is what actually crashed live
        conn.close()

    assert not result.empty
