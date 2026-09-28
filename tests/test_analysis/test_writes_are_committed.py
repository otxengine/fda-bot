"""Regression test for a real bug found during live end-to-end testing:
analysis/macro_regime.py, analysis/screener.py, analysis/sentiment.py, and
universe/sp500_fetch.py all wrote via raw conn.execute()/executemany() but
never called conn.commit(). Same-connection reads saw the data (SQLite makes
uncommitted writes visible to their own connection), which is why this
passed casual testing — but the dashboard reads through a SEPARATE
connection (see storage/db.py's read_connection()), which never saw the
write until something else happened to commit. This test opens a second,
independent connection to the same file and asserts the write is visible
there — the actual real-world scenario."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from analysis.macro_regime import compute_and_store_regime
from analysis.screener import run_screener
from analysis.sentiment import score_unscored_articles
from ingestion.models import MacroPoint, NewsArticle
from storage.db import init_schema, write_connection
from storage.writers import (
    append_chat_message,
    create_conversation,
    delete_conversation,
    upsert_macro_series,
    upsert_news_articles,
)
from universe.sp500_fetch import upsert_instruments


def _second_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def test_compute_and_store_regime_is_visible_from_another_connection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        conn = write_connection(db_path)
        # No macro_series data at all -> every category score is 0.0, a real
        # (if uninteresting) regime label — still exercises the write path.
        compute_and_store_regime(conn)
        conn.close()

        other = _second_connection(db_path)
        count = other.execute("SELECT count(*) FROM macro_regime_history").fetchone()[0]
        other.close()
        assert count == 1


def test_run_screener_is_visible_from_another_connection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        conn = write_connection(db_path)
        upsert_instruments(conn, [{"symbol": "AAPL", "name": "Apple", "sector": "Tech", "industry": "Hardware"}])
        conn.execute(
            "INSERT INTO quotes_latest (symbol, price, pe_ratio, market_cap, as_of, source) VALUES (?, ?, ?, ?, ?, ?)",
            ("AAPL", 150.0, 20.0, 3e12, "2024-01-01", "test"),
        )
        conn.commit()
        run_screener(conn)
        conn.close()

        other = _second_connection(db_path)
        count = other.execute("SELECT count(*) FROM screener_snapshots").fetchone()[0]
        other.close()
        assert count == 1


def test_score_unscored_articles_is_visible_from_another_connection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        conn = write_connection(db_path)
        upsert_news_articles(
            conn,
            [
                NewsArticle(
                    article_id="a1", headline="Great earnings beat", url="https://example.com",
                    published_at="2024-01-01T00:00:00+00:00", source="test",
                )
            ],
        )
        score_unscored_articles(conn)
        conn.close()

        other = _second_connection(db_path)
        score = other.execute("SELECT sentiment_score FROM news_articles WHERE article_id = 'a1'").fetchone()[0]
        other.close()
        assert score is not None


def test_upsert_instruments_is_visible_from_another_connection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        conn = write_connection(db_path)
        upsert_instruments(conn, [{"symbol": "MSFT", "name": "Microsoft", "sector": "Tech", "industry": "Software"}])
        conn.close()

        other = _second_connection(db_path)
        count = other.execute("SELECT count(*) FROM instruments WHERE symbol = 'MSFT'").fetchone()[0]
        other.close()
        assert count == 1


def test_chat_conversation_persists_and_deletes_across_connections() -> None:
    """Backs dashboard/pages/5_Chat.py's persisted, multi-conversation
    history — a conversation + its messages saved in one connection (the
    dashboard's short-lived write) must be visible from another (a fresh
    page load's read), and deleting it must actually remove both rows, not
    just clear in-memory session state."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        conn = write_connection(db_path)
        create_conversation(conn, "conv-1", "Which sector looks strongest?")
        append_chat_message(conn, "conv-1", "user", "Which sector looks strongest?")
        append_chat_message(conn, "conv-1", "assistant", "Technology, per get_sector_rotation.")
        conn.close()

        other = _second_connection(db_path)
        conv_rows = other.execute("SELECT id, title FROM chat_conversations").fetchall()
        msg_rows = other.execute(
            "SELECT role, content FROM chat_history WHERE conversation_id = 'conv-1' ORDER BY id"
        ).fetchall()
        other.close()
        assert conv_rows == [("conv-1", "Which sector looks strongest?")]
        assert msg_rows == [
            ("user", "Which sector looks strongest?"),
            ("assistant", "Technology, per get_sector_rotation."),
        ]

        conn2 = write_connection(db_path)
        delete_conversation(conn2, "conv-1")
        conn2.close()

        other2 = _second_connection(db_path)
        conv_count = other2.execute("SELECT count(*) FROM chat_conversations").fetchone()[0]
        msg_count = other2.execute("SELECT count(*) FROM chat_history").fetchone()[0]
        other2.close()
        assert conv_count == 0
        assert msg_count == 0


def test_chat_history_migration_backfills_pre_existing_flat_rows() -> None:
    """Regression test for the schema migration in storage/db.py: a DB
    created under the earlier single-conversation chat_history (no
    conversation_id column) must not lose those rows when opened under the
    new multi-conversation schema — they get grouped into one
    "Earlier conversation" rather than silently dropped or crashing on the
    now-missing column."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"

        # Simulate the OLD schema by hand: create a bare chat_history table
        # with no conversation_id, and no chat_conversations table at all.
        pre_migration_conn = sqlite3.connect(str(db_path))
        pre_migration_conn.execute(
            "CREATE TABLE chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, content TEXT, created_at TIMESTAMP)"
        )
        pre_migration_conn.execute(
            "INSERT INTO chat_history (role, content, created_at) VALUES ('user', 'old message', '2024-01-01T00:00:00+00:00')"
        )
        pre_migration_conn.commit()
        pre_migration_conn.close()

        # Now open it the normal way — this runs init_schema(), which must
        # apply the migration rather than erroring on the pre-existing table.
        conn = write_connection(db_path)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(chat_history)").fetchall()]
        assert "conversation_id" in cols

        row = conn.execute("SELECT conversation_id, role, content FROM chat_history").fetchone()
        assert row[1:] == ("user", "old message")
        migrated_conversation_id = row[0]
        assert migrated_conversation_id is not None

        conv = conn.execute(
            "SELECT title FROM chat_conversations WHERE id = ?", [migrated_conversation_id]
        ).fetchone()
        assert conv == ("Earlier conversation",)
        conn.close()
