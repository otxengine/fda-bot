"""The connector interface contract (see plan's "Connector interface contract").

Every source module implements BaseConnector so connectors are truly
pluggable — a Phase 4 scraper can be disabled in config/connectors.yaml
without touching anything else. fetch() must NEVER raise: catch everything
internally and return a ConnectorResult with status="error" so one broken
connector can never take down the scheduler's job loop.
"""

from __future__ import annotations

import functools
import logging
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import yaml

from config.settings import get_settings

logger = logging.getLogger(__name__)

SourceTier = Literal["official_free", "official_paid", "unofficial_library", "scrape_fallback"]
ConnectorStatus = Literal["ok", "error", "partial", "disabled"]


@dataclass
class ConnectorResult:
    connector_name: str
    fetched_at: datetime
    status: ConnectorStatus
    raw_payload: Any = None
    error: str | None = None
    rows_hint: int = 0


@dataclass
class ConnectorConfig:
    enabled: bool = True
    tier: SourceTier = "official_free"
    cadence_seconds: int = 3600
    fetch_timeout_seconds: int = 20
    max_retries: int = 3


@functools.lru_cache
def _load_connectors_yaml() -> dict:
    path = get_settings().connectors_config_path
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_connector_config(name: str) -> ConnectorConfig:
    raw = _load_connectors_yaml().get("connectors", {}).get(name, {})
    return ConnectorConfig(
        enabled=raw.get("enabled", False),
        tier=raw.get("tier", "official_free"),
        cadence_seconds=raw.get("cadence_seconds", 3600),
        fetch_timeout_seconds=raw.get("fetch_timeout_seconds", 20),
        max_retries=raw.get("max_retries", 3),
    )


class BaseConnector(ABC):
    """Subclass and implement `_fetch_impl()`. Do the actual I/O there — the
    public `fetch()` wraps it with config lookup, a hard timeout, and a
    catch-all so this class's contract (never raises) always holds."""

    name: str

    def __init__(self) -> None:
        self.config = load_connector_config(self.name)

    @property
    def source_tier(self) -> SourceTier:
        return self.config.tier

    def is_enabled(self) -> bool:
        return self.config.enabled

    @abstractmethod
    def _fetch_impl(self) -> Any:
        """Do the actual network call(s) and return a raw payload. May raise —
        fetch() catches it. Must respect self.config.max_retries itself if it
        wants retry/backoff (e.g. via tenacity) beyond the outer timeout."""
        raise NotImplementedError

    def fetch(self) -> ConnectorResult:
        if not self.is_enabled():
            return ConnectorResult(
                connector_name=self.name,
                fetched_at=datetime.now(timezone.utc),
                status="disabled",
            )

        started = time.monotonic()
        # Hard timeout independent of any internal retry/backoff (tenacity etc.)
        # so one hung connector (e.g. a stuck Playwright page) can never stall
        # the scheduler's whole job queue. See plan's ADR-4.
        #
        # Deliberately NOT a `with ThreadPoolExecutor(...) as pool:` block: that
        # context manager's __exit__ calls shutdown(wait=True), which blocks
        # until the worker thread finishes — completely defeating the timeout
        # (confirmed in practice: a connector configured to time out at 30s
        # instead took 115s end-to-end, because the pool wait outlived the
        # future.result() timeout). shutdown(wait=False) below lets fetch()
        # actually return promptly; the orphaned worker thread is abandoned
        # and finishes on its own later, harmlessly discarded.
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(self._fetch_impl)
            raw = future.result(timeout=self.config.fetch_timeout_seconds)
            return ConnectorResult(
                connector_name=self.name,
                fetched_at=datetime.now(timezone.utc),
                status="ok",
                raw_payload=raw,
            )
        except FutureTimeoutError:
            logger.warning("%s: fetch() timed out after %ss", self.name, self.config.fetch_timeout_seconds)
            return ConnectorResult(
                connector_name=self.name,
                fetched_at=datetime.now(timezone.utc),
                status="error",
                error=f"timeout after {self.config.fetch_timeout_seconds}s",
            )
        except Exception as exc:  # noqa: BLE001 — deliberate catch-all, see class docstring
            logger.exception("%s: fetch() failed", self.name)
            return ConnectorResult(
                connector_name=self.name,
                fetched_at=datetime.now(timezone.utc),
                status="error",
                error=str(exc),
            )
        finally:
            pool.shutdown(wait=False)
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.debug("%s: fetch() took %dms", self.name, duration_ms)


class BaseNormalizer(ABC):
    """Pure transform: raw ConnectorResult payload -> canonical records
    (ingestion/models.py). Kept separate from the connector so normalizer
    logic can be unit-tested against recorded fixtures with no network."""

    @abstractmethod
    def normalize(self, result: ConnectorResult) -> list[Any]:
        raise NotImplementedError
