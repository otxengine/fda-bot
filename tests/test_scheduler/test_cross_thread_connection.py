"""Regression test for a real bug found in the live running background
service's own logs: "SQLite objects created in a thread can only be used in
that same thread." APScheduler dispatches every job through its own executor
thread(s) — a different OS thread than the one that called write_connection()
in scheduler/main.py's main(). This reproduces that exact scenario directly
(connection created in one thread, used from another) rather than needing to
spin up a real APScheduler instance."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from storage.db import write_connection


def test_write_connection_is_usable_from_a_different_thread_than_it_was_created_in() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = write_connection(Path(tmp) / "t.db")  # created in THIS (the test) thread

        errors: list[Exception] = []

        def use_from_other_thread() -> None:
            try:
                conn.execute("SELECT 1").fetchone()
                conn.execute(
                    "INSERT INTO connector_runs (connector_name, run_ts, status) VALUES (?, ?, ?)",
                    ("test", "2024-01-01T00:00:00+00:00", "ok"),
                )
                conn.commit()
            except Exception as exc:  # noqa: BLE001 — capturing to assert on, not swallowing
                errors.append(exc)

        worker = threading.Thread(target=use_from_other_thread)
        worker.start()
        worker.join(timeout=5)

        count = conn.execute("SELECT count(*) FROM connector_runs").fetchone()[0]
        conn.close()

    assert not errors, f"cross-thread use of the write connection raised: {errors!r}"
    assert count == 1
