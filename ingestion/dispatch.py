"""Routes a normalizer's mixed-type record list to the right storage writer
by isinstance(). Used by scheduler/jobs.py after every connector run."""

from __future__ import annotations

import sqlite3

from ingestion.models import (
    EarningsEstimate,
    EconomicRelease,
    FdaBotCpBucket,
    FdaBotPerformanceSnapshot,
    FdaBotScoreBucket,
    FdaBotSignal,
    FomcEvent,
    FundamentalsSnapshot,
    MacroPoint,
    NewsArticle,
    PriceBar,
    Quote,
)
from storage import writers


def write_records(conn: sqlite3.Connection, records: list[object]) -> int:
    price_bars: list[PriceBar] = []
    quotes: list[Quote] = []
    fundamentals: list[FundamentalsSnapshot] = []
    estimates: list[EarningsEstimate] = []
    macro_points: list[MacroPoint] = []
    news: list[NewsArticle] = []
    fomc: list[FomcEvent] = []
    econ_releases: list[EconomicRelease] = []
    fda_bot_perf: list[FdaBotPerformanceSnapshot] = []
    fda_bot_cp: list[FdaBotCpBucket] = []
    fda_bot_score: list[FdaBotScoreBucket] = []
    fda_bot_signals: list[FdaBotSignal] = []

    for r in records:
        if isinstance(r, PriceBar):
            price_bars.append(r)
        elif isinstance(r, Quote):
            quotes.append(r)
        elif isinstance(r, FundamentalsSnapshot):
            fundamentals.append(r)
        elif isinstance(r, EarningsEstimate):
            estimates.append(r)
        elif isinstance(r, MacroPoint):
            macro_points.append(r)
        elif isinstance(r, NewsArticle):
            news.append(r)
        elif isinstance(r, FomcEvent):
            fomc.append(r)
        elif isinstance(r, EconomicRelease):
            econ_releases.append(r)
        elif isinstance(r, FdaBotPerformanceSnapshot):
            fda_bot_perf.append(r)
        elif isinstance(r, FdaBotCpBucket):
            fda_bot_cp.append(r)
        elif isinstance(r, FdaBotScoreBucket):
            fda_bot_score.append(r)
        elif isinstance(r, FdaBotSignal):
            fda_bot_signals.append(r)

    total = 0
    total += writers.upsert_price_bars(conn, price_bars)
    total += writers.upsert_quotes_latest(conn, quotes)
    total += writers.append_fundamentals_history(conn, fundamentals)
    total += writers.append_earnings_estimates(conn, estimates)
    total += writers.upsert_macro_series(conn, macro_points)
    total += writers.upsert_news_articles(conn, news)
    total += writers.upsert_fomc_events(conn, fomc)
    total += writers.upsert_economic_releases(conn, econ_releases)
    total += writers.append_fda_bot_performance_snapshots(conn, fda_bot_perf)
    total += writers.append_fda_bot_cp_buckets(conn, fda_bot_cp)
    total += writers.append_fda_bot_score_buckets(conn, fda_bot_score)
    # Always call this one even with an empty list: it's a full replace (see
    # its docstring), so an empty /api/stock-signals response must still
    # clear out tickers whose events have passed.
    total += writers.replace_fda_bot_signals(conn, fda_bot_signals)
    return total
