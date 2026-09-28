"""Unit tests for the scheduler's PID-file lock — the mechanism that stops
run.bat and the always-on background task (run_background.bat, via Task
Scheduler) from ever running two scheduler processes as writers against the
same DB at once (see plan's ADR-2: exactly one writer by design). Also used
on Render (start_render.sh) via the same disk-backed PID file."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from scheduler.main import _pid_is_alive, is_scheduler_already_running


def test_no_pid_file_means_not_running(monkeypatch, tmp_path) -> None:
    pid_file = tmp_path / "scheduler.pid"
    monkeypatch.setattr("scheduler.main._pid_file_path", lambda: pid_file)
    assert is_scheduler_already_running() is False


def test_pid_file_with_own_pid_means_running(monkeypatch, tmp_path) -> None:
    # This test process is definitely alive — the simplest reliable "live PID".
    pid_file = tmp_path / "scheduler.pid"
    pid_file.write_text(str(os.getpid()))
    monkeypatch.setattr("scheduler.main._pid_file_path", lambda: pid_file)
    assert is_scheduler_already_running() is True


def test_stale_pid_file_means_not_running(monkeypatch, tmp_path) -> None:
    # A PID essentially guaranteed not to correspond to a live process.
    pid_file = tmp_path / "scheduler.pid"
    pid_file.write_text("999999")
    monkeypatch.setattr("scheduler.main._pid_file_path", lambda: pid_file)
    assert is_scheduler_already_running() is False


def test_garbage_pid_file_does_not_crash(monkeypatch, tmp_path) -> None:
    pid_file = tmp_path / "scheduler.pid"
    pid_file.write_text("not-a-pid")
    monkeypatch.setattr("scheduler.main._pid_file_path", lambda: pid_file)
    assert is_scheduler_already_running() is False


# ── POSIX branch (Render's Linux container) — this test suite normally runs
# on the developer's Windows machine, which would otherwise only ever
# exercise the tasklist branch above. Force sys.platform so the os.kill()
# branch (added for Render, see start_render.sh) is actually tested here
# too, not just assumed correct by inspection.

def test_posix_branch_live_pid(monkeypatch) -> None:
    import scheduler.main as sched_main

    monkeypatch.setattr(sched_main.sys, "platform", "linux")
    calls = []

    def fake_kill(pid, sig):
        calls.append((pid, sig))  # no exception raised == "alive"

    monkeypatch.setattr(sched_main.os, "kill", fake_kill)
    assert _pid_is_alive(12345) is True
    assert calls == [(12345, 0)]


def test_posix_branch_dead_pid(monkeypatch) -> None:
    import scheduler.main as sched_main

    monkeypatch.setattr(sched_main.sys, "platform", "linux")

    def fake_kill(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(sched_main.os, "kill", fake_kill)
    assert _pid_is_alive(12345) is False


def test_posix_branch_pid_owned_by_another_user(monkeypatch) -> None:
    """A PermissionError means the PID exists (just not ours) — must still
    count as "alive" so the lock correctly refuses a second writer."""
    import scheduler.main as sched_main

    monkeypatch.setattr(sched_main.sys, "platform", "linux")

    def fake_kill(pid, sig):
        raise PermissionError

    monkeypatch.setattr(sched_main.os, "kill", fake_kill)
    assert _pid_is_alive(12345) is True
