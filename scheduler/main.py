"""Scheduler process entrypoint. Holds the ONE long-lived DuckDB write
connection for the app's whole session (see plan's ADR-2/ADR-3) and runs
every job on its own interval via APScheduler.

Every job is registered with max_instances=1 and coalesce=True so an
overlapping/slow run can never race another instance of itself on the
DuckDB write lock (see plan's ADR-4). fetch()'s own hard timeout (in
connectors/base.py) is what keeps one hung connector from blocking the
whole schedule.

Run: `python -m scheduler.main` directly, via run.bat (interactive session,
alongside the dashboard), or as the always-on background service started by
Task Scheduler (see run_background.bat / install_background_task.ps1) — the
PID-file check below (is_scheduler_already_running) exists specifically so
those three launch paths can never end up with two scheduler processes
writing to the same DB at once.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from apscheduler.executors.pool import ThreadPoolExecutor as APSThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import get_settings
from connectors.base import load_connector_config
from scheduler import jobs
from storage.db import write_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _pid_file_path() -> Path:
    return get_settings().db_path.parent / "scheduler.pid"


def is_scheduler_already_running() -> bool:
    """True if a previous scheduler process's PID file points at a PID that's
    still alive. Used by run.bat and run_background.bat to avoid two
    concurrent writers on the same DB (see plan's ADR-2 — this app has
    exactly one writer by design, not a locking mechanism that tolerates more).

    Also used on Render (see start_render.sh) — the disk-backed PID file is
    what stops a redeploy/restart from ever briefly running two scheduler
    processes against the same mounted SQLite file."""
    pid_file = _pid_file_path()
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return False
    return _pid_is_alive(pid)


def _pid_is_alive(pid: int) -> bool:
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, check=False
        )
        return str(pid) in result.stdout
    # POSIX (Render's Linux container, macOS): signal 0 checks a PID's
    # existence/permissions without actually sending a signal to it.
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by a different user


def _write_pid_file() -> None:
    pid_file = _pid_file_path()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))


def _remove_pid_file() -> None:
    try:
        _pid_file_path().unlink(missing_ok=True)
    except OSError:
        pass

# (job function, connector-config name it inherits cadence from) for fetch jobs;
# derived/analysis jobs get a fixed cadence directly.
FETCH_JOBS = [
    (jobs.job_fred, "fred"),
    (jobs.job_fred_calendar, "fred_calendar"),
    (jobs.job_bls, "bls"),
    (jobs.job_bea, "bea"),
    (jobs.job_worldbank, "worldbank"),
    (jobs.job_federal_reserve, "federal_reserve"),
    (jobs.job_fda_bot, "fda_bot"),
    (jobs.job_yahoo_finance_priority, "yahoo_finance_priority"),
    (jobs.job_yahoo_finance_universe, "yahoo_finance_universe"),
]

DERIVED_JOBS = [
    (jobs.job_macro_regime, 3600),       # hourly, after the macro fetch jobs
    (jobs.job_screener, 3600),           # hourly, per plan's Analysis Design
    (jobs.job_sentiment, 1800),          # every 30 min
    (jobs.job_refresh_sp500_universe, 604800),  # weekly
]


def main() -> None:
    if is_scheduler_already_running():
        logger.warning(
            "A scheduler process is already running (see %s) — refusing to start a second "
            "writer against the same DB. If that's stale (e.g. the machine crashed), delete "
            "the .pid file and retry.",
            _pid_file_path(),
        )
        return

    _write_pid_file()
    conn = write_connection()
    # Single-worker executor: APScheduler's default executor runs jobs in its
    # OWN thread pool, separate from this main thread — but sqlite3.Connection
    # objects (with the default check_same_thread=True) can only be used from
    # the thread that created them. With the default multi-worker executor,
    # `conn` (created here, in the main thread) got handed to jobs running in
    # whichever pool thread APScheduler picked, raising "SQLite objects
    # created in a thread can only be used in that same thread" — confirmed
    # live, intermittently, in the real background service's own logs. One
    # worker thread means every job always runs in that same thread as every
    # other job, so `conn` only ever sees a single, consistent thread.
    scheduler = BlockingScheduler(executors={"default": APSThreadPoolExecutor(max_workers=1)})

    def run_all_catchup() -> None:
        """Immediate catch-up fetch on launch so the dashboard isn't staring
        at stale data (see plan's Architecture section)."""
        logger.info("Running startup catch-up fetch for all enabled jobs...")
        for job_fn, _ in FETCH_JOBS:
            _safe_run(job_fn, conn)
        for job_fn, _ in DERIVED_JOBS:
            _safe_run(job_fn, conn)
        logger.info("Catch-up fetch complete.")

    for job_fn, connector_name in FETCH_JOBS:
        cfg = load_connector_config(connector_name)
        scheduler.add_job(
            _safe_run,
            trigger=IntervalTrigger(seconds=cfg.cadence_seconds),
            args=[job_fn, conn],
            id=f"fetch_{connector_name}",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=cfg.cadence_seconds,
        )

    for job_fn, cadence_seconds in DERIVED_JOBS:
        scheduler.add_job(
            _safe_run,
            trigger=IntervalTrigger(seconds=cadence_seconds),
            args=[job_fn, conn],
            id=f"derived_{job_fn.__name__}",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=cadence_seconds,
        )

    run_all_catchup()

    logger.info("Scheduler started. DB: %s", get_settings().db_path)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down scheduler...")
    finally:
        conn.close()
        _remove_pid_file()


def _safe_run(job_fn, conn, *args) -> None:
    """Every job call goes through this so one job's exception can never take
    down the scheduler loop (mirrors the "fetch() never raises" contract one
    level up, for the derived/analysis jobs that aren't connectors)."""
    try:
        job_fn(conn, *args)
    except Exception:  # noqa: BLE001 — deliberate catch-all, see docstring
        logger.exception("job %s failed", getattr(job_fn, "__name__", job_fn))


if __name__ == "__main__":
    main()
