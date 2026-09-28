"""SQLite connection helpers.

NOTE: the plan originally chose DuckDB with SQLite as a documented fallback,
gated on a Phase-1 concurrency smoke test (tests/smoke/test_db_concurrency.py).
That test FAILED on this machine: DuckDB's file lock blocks a separate-process
read-only connection while the writer process holds the file open (confirmed
via two real OS subprocesses, not just threads — see the test's docstring for
the reproduction). Per the plan's ADR-2, we take the documented fallback:
SQLite in WAL mode, which is built for exactly this one-writer/many-readers
pattern across separate processes.

Two-process access pattern (still holds, see plan's ADR-2/ADR-3):
- The scheduler process is the ONLY writer, with one long-lived write
  connection for its whole run.
- The dashboard process opens a fresh read-only-by-convention connection per
  query (via `read_connection()`) rather than holding one open.
"""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import date, datetime
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Python 3.12 deprecated sqlite3's implicit date/datetime adapters — register
# our own explicitly (ISO 8601 strings, which sort correctly and parse
# cleanly via pandas.to_datetime on read) rather than relying on defaults
# that will eventually be removed.
sqlite3.register_adapter(date, lambda d: d.isoformat())
sqlite3.register_adapter(datetime, lambda dt: dt.isoformat())


def default_db_path() -> Path:
    """Resolve the DB path from settings, falling back to ./data/finresearch.duckdb."""
    from config.settings import get_settings

    return get_settings().db_path


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply schema.sql. Safe to call repeatedly (all statements are IF NOT EXISTS)."""
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(ddl)
    conn.commit()
    _migrate_chat_history_conversation_id(conn)
    _migrate_fda_bot_target_columns(conn)


def _migrate_chat_history_conversation_id(conn: sqlite3.Connection) -> None:
    """One-off migration: an early version of chat_history had no
    conversation_id (single flat history, before multi-conversation support
    was added). `CREATE TABLE IF NOT EXISTS` in schema.sql is a no-op against
    an already-existing table, so a DB created under that earlier version
    needs this explicit ALTER — and any pre-migration rows get grouped into
    one "Earlier conversation" rather than silently dropped."""
    import uuid
    from datetime import datetime, timezone

    cols = [row[1] for row in conn.execute("PRAGMA table_info(chat_history)").fetchall()]
    if "conversation_id" in cols:
        return
    conn.execute("ALTER TABLE chat_history ADD COLUMN conversation_id TEXT")
    orphaned = conn.execute("SELECT count(*) FROM chat_history WHERE conversation_id IS NULL").fetchone()[0]
    if orphaned:
        default_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO chat_conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (default_id, "Earlier conversation", now, now),
        )
        conn.execute("UPDATE chat_history SET conversation_id = ? WHERE conversation_id IS NULL", (default_id,))
    conn.commit()


def _migrate_fda_bot_target_columns(conn: sqlite3.Connection) -> None:
    """One-off migrations for fda-bot integration columns added after their
    tables first shipped — `CREATE TABLE IF NOT EXISTS` is a no-op against
    an already-existing table, so a DB created before each of these needs
    its own explicit ALTER. Each block is gated independently: don't assume
    running one implies the other has (or hasn't) already run."""
    perf_cols = [row[1] for row in conn.execute("PRAGMA table_info(fda_bot_performance_snapshots)").fetchall()]
    if perf_cols and "n_to_target" not in perf_cols:
        # fda-bot added entry->planned-exit outcome tracking (2026-09-28) —
        # see ingestion/models.py's FdaBotPerformanceSnapshot.
        conn.execute("ALTER TABLE fda_bot_performance_snapshots ADD COLUMN n_to_target INTEGER")
        conn.execute("ALTER TABLE fda_bot_performance_snapshots ADD COLUMN win_rate_to_target DOUBLE")
        conn.execute("ALTER TABLE fda_bot_performance_snapshots ADD COLUMN avg_return_to_target DOUBLE")

    sig_cols = [row[1] for row in conn.execute("PRAGMA table_info(fda_bot_signals)").fetchall()]
    if sig_cols and "sector_momentum" not in sig_cols:
        # fda-bot added the sector/macro conviction overlay (2026-09-28) —
        # see ingestion/models.py's FdaBotSignal.
        conn.execute("ALTER TABLE fda_bot_signals ADD COLUMN sector_momentum TEXT")
        conn.execute("ALTER TABLE fda_bot_signals ADD COLUMN macro_risk_flag TEXT")

    conn.commit()


def _configure(conn: sqlite3.Connection) -> None:
    # WAL mode is what makes one writer + many concurrent readers across
    # separate OS processes actually work (this is the whole reason we're on
    # SQLite instead of DuckDB — see module docstring).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")


def write_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open (and create if needed) the DB for writing. Caller owns the connection's
    lifecycle — intended for one long-lived instance in the scheduler process.

    check_same_thread=False: this connection is created once in main() but
    then handed to every scheduled job, which APScheduler dispatches through
    its OWN executor thread(s) — a genuinely different OS thread than the one
    that created the connection. sqlite3's default same-thread check doesn't
    know or care that scheduler/main.py's single-worker executor (see that
    module) guarantees jobs never actually run concurrently; it just sees
    "different thread" and refuses, which is what broke live in the running
    background service ("SQLite objects created in a thread can only be used
    in that same thread"). Disabling the check is safe specifically BECAUSE
    that single-worker executor + max_instances=1 already serialize every
    access — this flag alone, without that serialization, would NOT be safe."""
    path = Path(db_path) if db_path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
    _configure(conn)
    init_schema(conn)
    return conn


@contextlib.contextmanager
def read_connection(db_path: Path | str | None = None):
    """Open a short-lived connection for reads. Use one per query/page-load
    from the dashboard process — never hold this open across reruns. WAL mode
    lets this run concurrently with the scheduler's writer without blocking."""
    path = Path(db_path) if db_path else default_db_path()
    conn = sqlite3.connect(str(path), timeout=5.0)
    _configure(conn)
    try:
        yield conn
    finally:
        conn.close()
