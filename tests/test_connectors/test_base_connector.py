"""Regression test for a real bug found during Phase-1 smoke testing: a
connector's hard fetch() timeout must actually let the caller move on
promptly, not block until the orphaned worker thread finishes on its own
(see connectors/base.py's fetch() docstring/comment for the full story —
worldbank's timeout was configured at 30s but the job actually took 115s
end-to-end before this fix)."""

from __future__ import annotations

import time

from connectors.base import BaseConnector, ConnectorConfig


class _SlowConnector(BaseConnector):
    name = "slow_test_connector"

    def __init__(self, sleep_seconds: float, timeout_seconds: float) -> None:
        # Bypass the real config.yaml load — this connector doesn't exist there.
        self.config = ConnectorConfig(enabled=True, fetch_timeout_seconds=timeout_seconds)
        self._sleep_seconds = sleep_seconds

    def _fetch_impl(self):
        time.sleep(self._sleep_seconds)
        return "should never be seen"


def test_fetch_returns_promptly_on_timeout_not_after_full_work_completes() -> None:
    connector = _SlowConnector(sleep_seconds=3.0, timeout_seconds=0.3)

    started = time.monotonic()
    result = connector.fetch()
    elapsed = time.monotonic() - started

    assert result.status == "error"
    assert "timeout" in (result.error or "")
    # The whole point of the fix: fetch() must return close to the configured
    # timeout, not wait out the full 3s of underlying work.
    assert elapsed < 1.5, f"fetch() took {elapsed:.2f}s — timeout isn't actually cutting the wait short"
