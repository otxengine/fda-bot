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

    # Stock signal (for stock trading, not options)
    stock_signal      = Column(String, nullable=True)   # BUY / WATCH / AVOID
    stock_signal_reason = Column(String, nullable=True)
    entry_price       = Column(Float, nullable=True)
    stop_loss_price   = Column(Float, nullable=True)    # entry × 0.92
    target_date       = Column(String, nullable=True)   # event_date - 1 day ISO


class AlertLog(Base):
    __tablename__ = "alert_log"

    id              = Column(Integer, primary_key=True, index=True)
    ticker          = Column(String, index=True)
    alert_type      = Column(String)
    triggered_at    = Column(DateTime, default=datetime.utcnow)
    score_at_trigger = Column(Float, nullable=True)
    message         = Column(Text)
    acknowledged    = Column(Integer, default=0)


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
