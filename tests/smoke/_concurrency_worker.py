"""Helper subprocess for test_db_concurrency.py. Must be a genuinely separate
OS process (not a thread) to actually exercise the real architecture: the
scheduler and dashboard are two separate processes."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from storage.db import read_connection, write_connection  # noqa: E402


def run_writer(db_path: str, duration_seconds: float) -> None:
    conn = write_connection(db_path)
    deadline = time.monotonic() + duration_seconds
    i = 0
    while time.monotonic() < deadline:
        conn.execute(
            "INSERT INTO macro_series (series_id, date, value, source, fetched_at) "
            "VALUES (?, ?, ?, 'test', ?) ON CONFLICT (series_id, date) DO UPDATE SET value = excluded.value",
            ("TEST:CONCURRENCY", date.today(), float(i), datetime.now(timezone.utc)),
        )
        conn.commit()
        i += 1
        time.sleep(0.05)
    conn.close()
    print("WRITER_OK")


def run_reader(db_path: str, duration_seconds: float) -> None:
    deadline = time.monotonic() + duration_seconds
    reads = 0
    while time.monotonic() < deadline:
        with read_connection(db_path) as conn:
            conn.execute("SELECT count(*) FROM macro_series").fetchone()
        reads += 1
        time.sleep(0.05)
    print(f"READER_OK reads={reads}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["writer", "reader"], required=True)
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--duration", type=float, default=3.0)
    args = parser.parse_args()

    if args.mode == "writer":
        run_writer(args.db_path, args.duration)
    else:
        time.sleep(0.5)  # give the writer a moment to create the schema first
        run_reader(args.db_path, args.duration)
