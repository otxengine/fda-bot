"""Canonical pydantic models — the shape every normalizer converts a source's
raw payload INTO, and the shape storage/writers.py writes FROM. Keeps every
downstream layer (analysis, dashboard) ignorant of per-source raw formats."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class PriceBar(BaseModel):
    symbol: str
    ts: datetime
    interval: str  # '1d','1h','15m'
    open: float
    high: float
    low: float
    close: float
    volume: int
    source: str


class Quote(BaseModel):
    symbol: str
    price: float
    change_pct: float | None = None
    volume: int | None = None
    market_cap: float | None = None
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    dividend_yield: float | None = None
    fifty_two_wk_high: float | None = None
    fifty_two_wk_low: float | None = None
    as_of: datetime
    source: str


class FundamentalsSnapshot(BaseModel):
    symbol: str
    as_of: datetime
    pe_ratio: float | None = None
    forward_pe: float | None = None
    peg_ratio: float | None = None
    eps: float | None = None
    revenue_ttm: float | None = None
    revenue_growth_yoy: float | None = None
    profit_margin: float | None = None
    debt_to_equity: float | None = None
    free_cash_flow: float | None = None
    roe: float | None = None
    source: str


class EarningsEstimate(BaseModel):
    """Analyst consensus estimate for one symbol/metric/period, e.g. 'what do
    analysts expect AAPL's revenue to be next fiscal year, and how fast is
    that growing YoY'. Appended (not upserted-to-latest) like
    FundamentalsSnapshot, so estimate revisions over time are visible."""

    symbol: str
    as_of: datetime
    period: str  # '0q' (this quarter), '+1q', '0y' (this fiscal year), '+1y' (next fiscal year)
    metric: str  # 'earnings' or 'revenue'
    avg_estimate: float | None = None
    growth_yoy: float | None = None  # consensus estimate's YoY growth rate for this period
    number_of_analysts: int | None = None
    source: str


class MacroPoint(BaseModel):
    series_id: str  # e.g. 'FRED:FEDFUNDS'
    date: date
    value: float
    source: str


class NewsArticle(BaseModel):
    article_id: str  # hash of URL
    symbol: str | None = None
    headline: str
    url: str
    published_at: datetime
    source: str
    sentiment_score: float | None = None


class FomcEvent(BaseModel):
    meeting_date: date
    is_two_day: bool = False


class EconomicRelease(BaseModel):
    """One scheduled economic data release (e.g. "CPI on 2026-10-14"), from
    FRED's official release calendar — see connectors/fred.py's
    FredCalendarConnector / WATCHED_RELEASE_IDS."""

    release_id: int
    release_name: str
    release_date: date


class FdaBotPerformanceSnapshot(BaseModel):
    """Point-in-time read of the user's own separate fda-bot service's
    alert-outcome performance (github.com/otxengine/fda-bot, /api/performance)
    — an external tool this app observes, not something it computes itself."""

    fetched_at: datetime
    total_alerts_tracked: int
    n_with_1d: int
    win_rate_1d: float | None = None
    win_rate_3d: float | None = None
    avg_return_1d: float | None = None
    avg_return_3d: float | None = None
    # entry -> planned pre-event exit (fda-bot's AlertLog.target_date) — the
    # trade its alerts actually recommend (exit before the FDA decision,
    # never hold through it), added to fda-bot 2026-09-28. Prefer these over
    # win_rate_1d/3d and avg_return_1d/3d above once n_to_target is large
    # enough to trust — see dashboard/pages/7_FDA_Bot.py's Diagnostics tab.
    n_to_target: int = 0
    win_rate_to_target: float | None = None
    avg_return_to_target: float | None = None
    upcoming_events: int
    events_next_7d: int
    total_signals: int
    historical_records: int
    last_scan_at: datetime | None = None


class FdaBotCpBucket(BaseModel):
    """One row of fda-bot's win-rate-by-call/put-ratio breakdown."""

    fetched_at: datetime
    bucket: str
    n: int
    win_rate: float | None = None
    avg_change: float | None = None


class FdaBotScoreBucket(BaseModel):
    """One row of fda-bot's win-rate-by-composite-score breakdown."""

    fetched_at: datetime
    range: str
    n: int
    p_up5: float | None = None
    p_up10: float | None = None
    p_down5: float | None = None
    p_down10: float | None = None
    avg_change: float | None = None
    median_change: float | None = None


class FdaBotSignal(BaseModel):
    """Latest 0-7 day actionable signal for one ticker, from fda-bot's
    /api/stock-signals."""

    ticker: str
    company: str | None = None
    event_type: str | None = None
    event_date: date | None = None
    days_until: int | None = None
    stock_signal: str | None = None
    stock_signal_reason: str | None = None
    entry_price: float | None = None
    stop_loss_price: float | None = None
    target_date: str | None = None
    composite_score: float | None = None
    expected_move_pct: float | None = None
    entry_window: str | None = None
    call_put_ratio: float | None = None
    iv_rank: float | None = None
    premium_flow: float | None = None
    liquidity_warning: bool = False
    iv_crush_warning: bool = False
    # Biotech sector strength (XBI vs SPY) / macro risk read, added to
    # fda-bot 2026-09-28 as a conviction overlay — see
    # backend/data/macro_context.py in that repo.
    sector_momentum: str | None = None   # strong/neutral/weak/unknown
    macro_risk_flag: str | None = None   # risk_on/neutral/risk_off/unknown
    fetched_at: datetime


class InstrumentRef(BaseModel):
    symbol: str
    name: str | None = None
    asset_type: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    currency: str | None = None
