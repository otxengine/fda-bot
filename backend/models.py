from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Date, Boolean, Text
from pydantic import BaseModel
from typing import Optional
from backend.database import Base


# SQLAlchemy ORM Models

class FdaEvent(Base):
    __tablename__ = "fda_events"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True, nullable=True)
    company = Column(String, nullable=False)
    event_type = Column(String)          # PDUFA, AdCom, NDA, BLA, etc.
    drug_name = Column(String, nullable=True)
    indication = Column(String, nullable=True)
    event_date = Column(Date, nullable=False)
    source = Column(String)              # fda.gov, biopharmawatch
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # BPC real-time enrichment (updated every 2h from BiopharmCatalyst API)
    bpc_price        = Column(Float, nullable=True)   # current stock price
    bpc_change_pct   = Column(Float, nullable=True)   # today's % change (real-time)
    bpc_rel_volume   = Column(Float, nullable=True)   # volume / 20d avg (spike detector)
    bpc_volume       = Column(Float, nullable=True)   # today's volume
    bpc_avg_volume   = Column(Float, nullable=True)   # 20-day avg volume
    bpc_optionable   = Column(Integer, nullable=True) # 1=has options, 0=stock-only
    bpc_market_cap   = Column(Float, nullable=True)
    bpc_insider_pct  = Column(Float, nullable=True)   # insider holdings %
    bpc_float        = Column(Float, nullable=True)   # shares float
    bpc_price_to_book = Column(Float, nullable=True)
    bpc_approval_prob = Column(Float, nullable=True)  # historical_loa: likelihood of approval
    bpc_prog_prob    = Column(Float, nullable=True)   # historical_pop: probability of progression
    bpc_months_cash  = Column(Float, nullable=True)   # estimated months of cash (paid tier)
    bpc_net_cash     = Column(Float, nullable=True)
    bpc_cash_burn    = Column(Float, nullable=True)   # monthly burn rate
    bpc_trial_id     = Column(String, nullable=True)  # ClinicalTrials NCT ID
    bpc_next_label   = Column(String, nullable=True)  # "Topline Data", "PDUFA Decision"
    bpc_fda_status   = Column(String, nullable=True)  # "Ongoing", "Approved", etc.


class OptionsSignal(Base):
    __tablename__ = "options_signals"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    fda_event_id = Column(Integer, nullable=True)
    scan_time = Column(DateTime, default=datetime.utcnow)

    # Raw metrics
    call_volume = Column(Float, default=0)
    put_volume = Column(Float, default=0)
    total_volume = Column(Float, default=0)
    open_interest = Column(Float, default=0)
    implied_volatility = Column(Float, default=0)
    iv_rank = Column(Float, default=0)      # 0-100
    stock_price = Column(Float, default=0)
    market_cap = Column(Float, default=0)

    # Computed signals
    vol_oi_ratio = Column(Float, default=0)
    call_put_ratio = Column(Float, default=0)
    premium_flow = Column(Float, default=0)  # in dollars
    composite_score = Column(Float, default=0)  # 0-100

    # Expiration analysis (new)
    event_pinned_ratio = Column(Float, default=0)       # 0-1: % weighted vol in event-proximal expiries
    expiration_score   = Column(Float, default=0)       # 0-100
    best_expiry        = Column(String, nullable=True)  # "2026-06-21"
    dominant_strike_type = Column(String, nullable=True) # atm/otm/deep_otm
    expiration_breakdown_json = Column(Text, nullable=True)  # JSON

    # Probability outputs (new)
    p_up_5         = Column(Float, nullable=True)   # P(+5%)
    p_up_10        = Column(Float, nullable=True)   # P(+10%)
    p_down_5       = Column(Float, nullable=True)   # P(-5%)
    p_down_10      = Column(Float, nullable=True)   # P(-10%)
    p_calibration_n = Column(Integer, default=0)
    p_confidence   = Column(String, nullable=True)  # high/medium/low

    # Alert level
    alert_level = Column(String, default="green")  # green, orange, red

    # Phase A additions
    expected_move_pct  = Column(Float, nullable=True)
    entry_window       = Column(String, nullable=True)   # early/optimal/late/avoid
    liquidity_warning  = Column(Integer, default=0)
    iv_crush_warning   = Column(Integer, default=0)
    earnings_overlap   = Column(Integer, default=0)
    flow_velocity      = Column(Float, default=0)

    # Phase B additions
    recommended_strategy = Column(String, nullable=True)
    strategy_rationale   = Column(String, nullable=True)
    strategy_conviction  = Column(String, nullable=True)  # high/medium/low

    # Event date — stored directly so signals are self-contained without joining FdaEvent
    event_date  = Column(Date, nullable=True)           # actual FDA/PDUFA/earnings date
    event_type  = Column(String, nullable=True)         # PDUFA / Phase 3 / Earnings / etc.

    # Stock signal (for stock trading, not options)
    stock_signal      = Column(String, nullable=True)   # BUY / WATCH / AVOID / BEARISH
    stock_signal_reason = Column(String, nullable=True)
    entry_price       = Column(Float, nullable=True)
    stop_loss_price   = Column(Float, nullable=True)    # entry × 0.92
    target_date       = Column(String, nullable=True)   # exit date (event_date - 1 day) ISO
    binary_event_risk = Column(Integer, default=0)      # small-cap + event ≤3d ahead
    trade_type        = Column(String,  nullable=True)  # "day" (0-2d) | "swing" (3-7d)

    # Fundamental analysis
    fundamental_score = Column(Float, nullable=True)    # 0-100
    cash_warning      = Column(Integer, default=0)      # <6 months cash
    squeeze_setup     = Column(Integer, default=0)      # high short interest
    analyst_bullish   = Column(Integer, default=0)      # analyst consensus buy
    clinical_score    = Column(Float, nullable=True)    # 0-100 (ClinicalTrials + OpenFDA)
    trial_risk        = Column(Integer, default=0)      # trial stopped for safety/futility
    strong_trial      = Column(Integer, default=0)      # completed with results


class AlertLog(Base):
    __tablename__ = "alert_log"

    id              = Column(Integer, primary_key=True, index=True)
    ticker          = Column(String, index=True)
    alert_type      = Column(String)
    triggered_at    = Column(DateTime, default=datetime.utcnow)
    score_at_trigger = Column(Float, nullable=True)
    message         = Column(Text)
    acknowledged    = Column(Integer, default=0)


class AlertOutcome(Base):
    """
    Tracks actual price outcomes for every BUY alert sent.
    Populated by run_alert_outcome_tracker() daily job.
    Used by learning engine to measure signal quality and adjust thresholds.
    """
    __tablename__ = "alert_outcomes"

    id             = Column(Integer, primary_key=True, index=True)
    alert_log_id   = Column(Integer, nullable=True)   # FK to alert_log.id
    ticker         = Column(String, index=True)
    alert_type     = Column(String)                   # stock_buy / penny_catalyst / already_moving
    alert_time     = Column(DateTime)
    alert_score    = Column(Float, nullable=True)
    price_at_alert = Column(Float, nullable=True)     # price when alert fired
    price_1d_after = Column(Float, nullable=True)
    price_3d_after = Column(Float, nullable=True)
    change_1d_pct  = Column(Float, nullable=True)
    change_3d_pct  = Column(Float, nullable=True)
    was_hit_1d     = Column(Integer, default=0)       # 1 = gained ≥5% within 1 day
    was_hit_3d     = Column(Integer, default=0)       # 1 = gained ≥5% within 3 days
    outcome_label  = Column(String, nullable=True)    # big_win/win/neutral/loss/big_loss
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LearningInsight(Base):
    """
    Stores LLM-generated insights: negative event flags + weight adjustments.
    Auto-expires via expires_at (checked at query time).
    """
    __tablename__ = "learning_insights"

    id           = Column(Integer, primary_key=True, index=True)
    ticker       = Column(String, nullable=True, index=True)   # None = global insight
    insight_type = Column(String, nullable=False)              # "negative_event" | "weight_adjustment"
    insight_json = Column(Text,   nullable=False)              # JSON payload
    confidence   = Column(Float,  default=0.5)
    sample_size  = Column(Integer, default=0)
    created_at   = Column(DateTime, default=datetime.utcnow)
    expires_at   = Column(DateTime, nullable=True)             # NULL = never expires


class HistoricalResult(Base):
    __tablename__ = "historical_results"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    company = Column(String)
    event_type = Column(String)
    drug_name = Column(String, nullable=True)
    event_date = Column(Date, nullable=False)
    source = Column(String)

    # Pre-event signal snapshot (from OptionsSignal before event)
    pre_event_score = Column(Float, nullable=True)
    pre_event_iv_rank = Column(Float, nullable=True)
    pre_event_call_put_ratio = Column(Float, nullable=True)
    pre_event_vol_oi_ratio = Column(Float, nullable=True)
    pre_event_premium_flow = Column(Float, nullable=True)
    pre_event_alert_level = Column(String, nullable=True)

    # Price data
    price_before = Column(Float, nullable=True)   # closing price day before event
    price_1d_after = Column(Float, nullable=True)
    price_3d_after = Column(Float, nullable=True)
    price_7d_after = Column(Float, nullable=True)
    change_1d_pct = Column(Float, nullable=True)
    change_3d_pct = Column(Float, nullable=True)
    change_7d_pct = Column(Float, nullable=True)

    # Outcome classification
    outcome = Column(String, nullable=True)  # "strong_up", "up", "neutral", "down", "strong_down"

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Pydantic Schemas

class FdaEventSchema(BaseModel):
    id: int
    ticker: Optional[str]
    company: str
    event_type: Optional[str]
    drug_name: Optional[str]
    indication: Optional[str]
    event_date: str
    source: str
    days_until: int

    class Config:
        from_attributes = True


class OptionsSignalSchema(BaseModel):
    id: int
    ticker: str
    company: Optional[str]
    fda_event_id: Optional[int]
    event_date: Optional[str]
    event_type: Optional[str]
    days_until: Optional[int]
    scan_time: str
    call_volume: float
    put_volume: float
    total_volume: float
    open_interest: float
    implied_volatility: float
    iv_rank: float
    stock_price: float
    market_cap: float
    vol_oi_ratio: float
    call_put_ratio: float
    premium_flow: float
    composite_score: float
    alert_level: str

    class Config:
        from_attributes = True


class TickerDetailSchema(BaseModel):
    ticker: str
    company: Optional[str]
    stock_price: float
    market_cap: float
    fda_events: list
    latest_signal: Optional[OptionsSignalSchema]
    signal_breakdown: dict
