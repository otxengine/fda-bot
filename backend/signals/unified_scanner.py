"""
Unified FDA catalyst scanner.

Automatically chooses the right analysis method per ticker:

  ┌─ Has real options market (price > $3, expirations > 0)?
  │      └─ YES → Options-flow analysis (analyzer.py)
  │                 High C/P + elevated IV + expiration pinning → BUY
  │
  └─ No options OR price < $3 (penny/micro-cap)?
         └─ Volume-spike analysis (penny_catalyst_scanner.py)
                 Volume spike ×2+ today vs 20-day avg
                 + 3-day momentum building
                 + upcoming FDA event in DB  → BUY

Both paths produce the same output dict so downstream code
(scheduler, alerter, DB writer) handles them identically.
"""
import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def _has_real_options(ticker: str, price: float) -> bool:
    """Quick check: does this ticker have a real options market?"""
    if price < 0.50:
        return False
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        exps = t.options
        if not exps:
            return False
        # Verify there's actual open interest (not just listed but dead options)
        try:
            chain = t.option_chain(exps[0])
            total_oi = chain.calls["openInterest"].sum() + chain.puts["openInterest"].sum()
            return total_oi >= 50
        except Exception:
            return len(exps) >= 1
    except Exception:
        return False


def scan_one(
    ticker: str,
    event_date: Optional[date],
    event_type: Optional[str],
    company: Optional[str],
    drug_name: Optional[str],
    db,
    fda_event_id: Optional[int],
    polygon_client=None,
    yfinance_client=None,
) -> Optional[dict]:
    """
    Unified entry point. Returns a BUY-ready signal dict or None.
    Automatically selects options path vs volume-spike path.
    """
    from backend.data.yfinance_client import YFinanceClient

    yf_client = yfinance_client or YFinanceClient()
    info = yf_client.get_stock_info(ticker)
    price = info.get("price", 0)
    market_cap = info.get("market_cap", 0)

    use_options_path = (
        price >= 1.0                      # lowered from $3 — many legit biotechs trade $1-3
        and (market_cap or 0) >= 5_000_000
        and _has_real_options(ticker, price)
    )

    if use_options_path:
        return _options_path(
            ticker, event_date, event_type, company, drug_name,
            db, fda_event_id, polygon_client, yf_client,
        )
    else:
        return _penny_path(ticker, event_date, event_type, company, price, market_cap)


def _options_path(ticker, event_date, event_type, company, drug_name,
                  db, fda_event_id, polygon_client, yf_client) -> Optional[dict]:
    """Full options-flow analysis via analyzer.py."""
    try:
        from backend.data.polygon import PolygonClient
        from backend.signals.analyzer import analyze_ticker

        poly = polygon_client or PolygonClient()
        result = analyze_ticker(
            ticker=ticker,
            polygon_client=poly,
            yfinance_client=yf_client,
            event_date=event_date,
            event_type=event_type,
            drug_name=drug_name,
            company=company,
            db=db,
            fda_event_id=fda_event_id,
        )
        if result:
            result["_scan_path"] = "options"
        return result
    except Exception as e:
        logger.debug(f"Options path failed {ticker}: {e}")
        return None


def _penny_path(ticker, event_date, event_type, company,
                price, market_cap) -> Optional[dict]:
    """Volume-spike analysis via penny_catalyst_scanner.py."""
    try:
        from backend.scrapers.penny_catalyst_scanner import scan_penny_ticker
        result = scan_penny_ticker(
            ticker=ticker,
            event_date=event_date,
            event_type=event_type,
            company=company,
        )
        if result is None:
            return None

        # Convert penny scanner output to the unified format expected by scheduler
        days_until = (event_date - date.today()).days if event_date else 999
        entry = result.get("price")
        stop  = round(entry * 0.85, 4) if entry else None   # tighter stop for volatile penny stocks

        return {
            # Identity
            "ticker":         ticker,
            "fda_event_id":   None,
            "scan_time":      __import__("datetime").datetime.utcnow(),
            # Pricing
            "stock_price":    entry or 0,
            "market_cap":     market_cap or 0,
            # Minimal options fields (not available — zeros)
            "call_volume":    0,
            "put_volume":     0,
            "total_volume":   result.get("volume_today", 0),
            "open_interest":  0,
            "implied_volatility": 0,
            "iv_rank":        0,
            "vol_oi_ratio":   0,
            "call_put_ratio": 0,
            "premium_flow":   0,
            # Scores (mapped from penny scoring)
            "composite_score":    result["composite_score"],
            "alert_level":        "orange" if result["composite_score"] >= 60 else "green",
            "expiration_score":   0,
            "event_pinned_ratio": 0,
            "best_expiry":        None,
            "dominant_strike_type": None,
            "expiration_breakdown_json": "[]",
            "expected_move_pct":  None,
            "p_up_5":    None, "p_up_10": None,
            "p_down_5":  None, "p_down_10": None,
            "p_calibration_n": 0, "p_confidence": "low",
            # Signal
            "stock_signal":        result["stock_signal"],
            "stock_signal_reason": result["reason"],
            "entry_price":         entry,
            "stop_loss_price":     stop,
            "target_date":         (event_date - timedelta(days=1)).isoformat() if event_date else None,
            "binary_event_risk":   int(result.get("risk_level") == "HIGH"),
            # Contextual
            "entry_window":       "optimal" if 0 <= days_until <= 3 else "early" if days_until <= 7 else "watch",
            "liquidity_warning":  0,
            "iv_crush_warning":   0,
            "earnings_overlap":   0,
            "flow_velocity":      0,
            "recommended_strategy": "buy_stock",
            "strategy_rationale":   f"volume spike ×{result.get('volume_spike',0):.1f} before FDA event",
            "strategy_conviction":  "high" if result["composite_score"] >= 70 else "medium",
            # Fundamental (not available in penny path)
            "fundamental_score": 50,
            "cash_warning":      0, "squeeze_setup": int(result.get("short_pct", 0) >= 20),
            "analyst_bullish":   0, "clinical_score": None,
            "trial_risk":        0, "strong_trial":   0,
            # Extra penny-specific fields (passed through for alerting)
            "_scan_path":        "penny",
            "_volume_spike":     result.get("volume_spike"),
            "_momentum_3d":      result.get("momentum_3d_pct"),
            "_short_pct":        result.get("short_pct"),
            "_news_count":       result.get("news_count_3d"),
            "_risk_level":       result.get("risk_level"),
            "_position_advice":  result.get("position_advice"),
            "_component_scores": result.get("component_scores", {}),
            "_weights":          {},
            "_rec_contract":     None,
            "_rec_exit":         None,
            "_fundamental_detail": {},
            "_neg_penalty":      0,
            "_neg_reason":       "",
            "_learned_wadj":     {},
        }
    except Exception as e:
        logger.debug(f"Penny path failed {ticker}: {e}")
        return None


def scan_all_events(
    db,
    days_window: int = 14,
    polygon_client=None,
    yfinance_client=None,
) -> list[dict]:
    """
    Scan ALL FDA events within days_window.
    Uses unified path selection per ticker.
    Returns list of signal dicts (only BUY signals).
    """
    from backend.models import FdaEvent

    today = date.today()
    cutoff = today + timedelta(days=days_window)

    events = db.query(FdaEvent).filter(
        FdaEvent.event_date >= today,
        FdaEvent.event_date <= cutoff,
        FdaEvent.ticker.isnot(None),
    ).all()

    if not events:
        return []

    # Deduplicate tickers — take earliest event per ticker
    seen: dict[str, object] = {}
    for e in sorted(events, key=lambda x: x.event_date):
        if e.ticker not in seen:
            seen[e.ticker] = e
    unique_events = list(seen.values())

    logger.info(f"Unified scan: {len(unique_events)} tickers over next {days_window}d")

    results = []
    for event in unique_events:
        sig = scan_one(
            ticker=event.ticker,
            event_date=event.event_date,
            event_type=event.event_type,
            company=event.company,
            drug_name=event.drug_name,
            db=db,
            fda_event_id=event.id,
            polygon_client=polygon_client,
            yfinance_client=yfinance_client,
        )
        if sig and sig.get("stock_signal") == "BUY":
            results.append(sig)

    results.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
    logger.info(f"Unified scan: {len(results)} BUY signals found")
    return results
