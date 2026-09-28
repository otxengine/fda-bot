"""One function per unit of scheduled work. Each connector job is a thin
wrapper: fetch -> normalize -> write -> log run. Kept here (not in main.py)
so they're independently importable/testable."""

from __future__ import annotations

import logging
import time

import sqlite3

from connectors.base import BaseConnector, BaseNormalizer
from ingestion.dispatch import write_records
from storage.writers import log_connector_run

logger = logging.getLogger(__name__)


def run_connector(conn: sqlite3.Connection, connector: BaseConnector, normalizer: BaseNormalizer) -> None:
    started = time.monotonic()
    result = connector.fetch()
    if result.status == "disabled":
        return
    records = normalizer.normalize(result) if result.status in ("ok", "partial") else []
    rows = write_records(conn, records) if records else 0
    duration_ms = int((time.monotonic() - started) * 1000)
    log_connector_run(conn, connector.name, result.status, rows, result.error, duration_ms)
    if result.status == "error":
        logger.warning("%s: fetch failed: %s", connector.name, result.error)
    else:
        logger.info("%s: ok, %d rows, %dms", connector.name, rows, duration_ms)


# --- MVP connector jobs ------------------------------------------------------

def job_fred(conn: sqlite3.Connection) -> None:
    from connectors.fred import FredConnector
    from ingestion.normalizers.fred import FredNormalizer

    run_connector(conn, FredConnector(), FredNormalizer())


def job_bls(conn: sqlite3.Connection) -> None:
    from connectors.bls import BlsConnector
    from ingestion.normalizers.bls import BlsNormalizer

    run_connector(conn, BlsConnector(), BlsNormalizer())


def job_bea(conn: sqlite3.Connection) -> None:
    from connectors.bea import BeaConnector
    from ingestion.normalizers.bea import BeaNormalizer

    run_connector(conn, BeaConnector(), BeaNormalizer())


def job_worldbank(conn: sqlite3.Connection) -> None:
    from connectors.worldbank import WorldBankConnector
    from ingestion.normalizers.worldbank import WorldBankNormalizer

    run_connector(conn, WorldBankConnector(), WorldBankNormalizer())


def job_federal_reserve(conn: sqlite3.Connection) -> None:
    from connectors.federal_reserve import FederalReserveConnector
    from ingestion.normalizers.federal_reserve import FederalReserveNormalizer

    run_connector(conn, FederalReserveConnector(), FederalReserveNormalizer())


def job_fred_calendar(conn: sqlite3.Connection) -> None:
    from connectors.fred import FredCalendarConnector
    from ingestion.normalizers.fred_calendar import FredCalendarNormalizer

    run_connector(conn, FredCalendarConnector(), FredCalendarNormalizer())


def job_fda_bot(conn: sqlite3.Connection) -> None:
    from connectors.fda_bot import FdaBotConnector
    from ingestion.normalizers.fda_bot import FdaBotNormalizer

    run_connector(conn, FdaBotConnector(), FdaBotNormalizer())


def _priority_symbols(conn: sqlite3.Connection) -> list[str]:
    """Watchlist + sector ETFs — small (~15 symbols), time-sensitive, its own
    frequent cadence. Kept SEPARATE from the full screener universe (see
    _universe_symbols) after live use showed bundling them meant the big
    fetch's timeout discarded the small, important one's results too — a
    timeout throws away the WHOLE batch, not just the slow tail."""
    from universe.sector_etfs import ALL_SECTOR_SYMBOLS

    watchlist = {r[0] for r in conn.execute("SELECT symbol FROM watchlist").fetchall()}
    return sorted(watchlist | set(ALL_SECTOR_SYMBOLS))


def _universe_symbols(conn: sqlite3.Connection) -> list[str]:
    """The full screener universe (S&P 500, once Phase 2's instruments table
    is populated) — large (~500 symbols), genuinely takes many minutes to
    fetch, so it gets its own slow cadence (see connectors.yaml) rather than
    starving the priority fetch above."""
    return sorted({r[0] for r in conn.execute("SELECT symbol FROM instruments").fetchall()})


def job_yahoo_finance_priority(conn: sqlite3.Connection) -> None:
    from connectors.yahoo_finance import YahooFinanceConnector
    from ingestion.normalizers.yahoo_finance import YahooFinanceNormalizer

    symbols = _priority_symbols(conn)
    if not symbols:
        logger.info("yahoo_finance_priority: no watchlist/sector symbols yet, skipping")
        return
    run_connector(
        conn,
        YahooFinanceConnector(symbols, connector_name="yahoo_finance_priority", include_estimates=True),
        YahooFinanceNormalizer(),
    )


def job_yahoo_finance_universe(conn: sqlite3.Connection) -> None:
    from connectors.yahoo_finance import YahooFinanceConnector
    from ingestion.normalizers.yahoo_finance import YahooFinanceNormalizer

    symbols = _universe_symbols(conn)
    if not symbols:
        logger.info("yahoo_finance_universe: screener universe not populated yet, skipping")
        return
    # include_estimates=True: the sector+quant idea-generation page
    # (analysis/sector_quant_screener.py) needs forward EPS/revenue
    # consensus across the WHOLE universe, not just the watchlist — this is
    # explicitly what makes the universe fetch slow (30-45 min) and why it's
    # on its own 3h cadence (see connectors.yaml).
    run_connector(
        conn,
        YahooFinanceConnector(symbols, connector_name="yahoo_finance_universe", include_estimates=True),
        YahooFinanceNormalizer(),
    )


# --- Derived/analysis jobs (run after the fetch jobs above) -----------------

def job_macro_regime(conn: sqlite3.Connection) -> None:
    from analysis.macro_regime import compute_and_store_regime

    compute_and_store_regime(conn)


def job_screener(conn: sqlite3.Connection) -> None:
    from analysis.screener import run_screener

    run_screener(conn)


def job_sentiment(conn: sqlite3.Connection) -> None:
    from analysis.sentiment import score_unscored_articles

    score_unscored_articles(conn)


def job_refresh_sp500_universe(conn: sqlite3.Connection) -> None:
    from universe.sp500_fetch import fetch_sp500_constituents, upsert_instruments

    records = fetch_sp500_constituents()
    upsert_instruments(conn, records)
    logger.info("sp500_universe: refreshed %d constituents", len(records))
