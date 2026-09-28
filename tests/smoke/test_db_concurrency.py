"""Phase 1's first task (per the plan): validate the two-process concurrent
access pattern — one long-lived writer (the scheduler) + short-lived
read-only-by-convention connections (the dashboard) against the SAME file,
as two genuinely separate OS processes — before building anything else on
top of it.

HISTORY: this test originally targeted DuckDB (the plan's first choice) and
FAILED on this Windows machine — DuckDB's file lock blocks a second
process's read-only connection while the writer process holds the file open
(confirmed via this exact two-subprocess reproduction). Per the plan's
documented ADR-2 fallback, storage/db.py now uses SQLite in WAL mode, and
THIS test validates that engine instead. It stays in the suite as a
regression check (see plan's Verification section).

Run directly: `python -m tests.smoke.test_db_concurrency`
Also runs under pytest as a slow/manual smoke test.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

WORKER = Path(__file__).parent / "_concurrency_worker.py"


def run_concurrency_check(duration_seconds: float = 3.0) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "concurrency_check.db")

        writer_proc = subprocess.Popen(
            [sys.executable, str(WORKER), "--mode", "writer", "--db-path", db_path,
             "--duration", str(duration_seconds)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        reader_proc = subprocess.Popen(
            [sys.executable, str(WORKER), "--mode", "reader", "--db-path", db_path,
             "--duration", str(duration_seconds)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        writer_out, writer_err = writer_proc.communicate(timeout=duration_seconds + 15)
        reader_out, reader_err = reader_proc.communicate(timeout=duration_seconds + 15)

        ok = (
            writer_proc.returncode == 0
            and reader_proc.returncode == 0
            and "WRITER_OK" in writer_out
            and "READER_OK" in reader_out
        )
        if not ok:
            raise AssertionError(
                "SQLite (WAL mode) concurrent writer + reader pattern (as two separate OS "
                "processes) is NOT reliable on this machine/SQLite version.\n"
                f"writer rc={writer_proc.returncode} stdout={writer_out!r} stderr={writer_err!r}\n"
                f"reader rc={reader_proc.returncode} stdout={reader_out!r} stderr={reader_err!r}\n"
                "-> This is the fallback engine the plan already accounted for having no further "
                "fallback; would need investigation (e.g. WAL file on a network drive, which "
                "doesn't support the required locking)."
            )


def test_sqlite_concurrent_write_and_read() -> None:
    run_concurrency_check(duration_seconds=2.0)


if __name__ == "__main__":
    print("Running SQLite (WAL mode) two-process (separate OS processes) concurrency check...")
    run_concurrency_check(duration_seconds=3.0)
    print("OK — concurrent writer + reader pattern works across separate processes.")
