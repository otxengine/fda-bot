"""
Signal scoring engine v2 — expiration-aware + probability calibrated.

Composite score weights:
  expiration_score  35%  (NEW — most important)
  iv_rank           20%
  call_put          20%
  vol_oi            15%
  premium_flow      10%
"""
import json
import logging
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Score weights (must sum to 100)
WEIGHT_EXPIRATION   = 30
WEIGHT_IV_RANK      = 17
WEIGHT_CALL_PUT     = 17
WEIGHT_VOL_OI       = 13
WEIGHT_PREMIUM      = 8
WEIGHT_FUNDAMENTAL  = 15

THRESHOLD_RED    = 70
THRESHOLD_ORANGE = 50

VOL_OI_CAP     = 3.0
CALL_PUT_CAP   = 5.0
PREMIUM_CAP    = 5_000_000


# ── Component scorers ─────────────────────────────────────────────────────────

def score_vol_oi(vol_oi: float) -> float:
    return min(100.0, (vol_oi / VOL_OI_CAP) * 100) if vol_oi > 0 else 0.0


def score_iv_rank(iv_rank: float) -> float:
    return max(0.0, min(100.0, float(iv_rank)))


def score_call_put(call_vol: float, put_vol: float) -> float:
    if put_vol <= 0:
        return 100.0 if call_vol > 0 else 50.0
    ratio = call_vol / put_vol
    if ratio < 1:
        return max(0.0, ratio * 50)
    return min(100.0, 50.0 + (ratio - 1) / (CALL_PUT_CAP - 1) * 50.0)


def score_premium(premium: float) -> float:
    return min(100.0, (premium / PREMIUM_CAP) * 100) if premium > 0 else 0.0


# ── Composite ─────────────────────────────────────────────────────────────────

def compute_composite_score(
    call_volume: float,
    put_volume: float,
    total_volume: float,
    open_interest: float,
    implied_volatility: float,
    iv_min: float,
    iv_max: float,
    premium_flow: float,
    expiration_score: float = 0.0,
    fundamental_score: float = 50.0,
) -> dict:
    vol_oi = total_volume / open_interest if open_interest > 0 else 0

    iv_range = iv_max - iv_min
    iv_rank = ((implied_volatility - iv_min) / iv_range * 100) if iv_range > 0 else 50.0
    iv_rank = max(0.0, min(100.0, iv_rank))

    call_put_ratio = (call_volume / put_volume) if put_volume > 0 else (2.0 if call_volume > 0 else 1.0)

    s_exp    = score_iv_rank(expiration_score)   # already 0-100
    s_iv     = score_iv_rank(iv_rank)
    s_cp     = score_call_put(call_volume, put_volume)
    s_voi    = score_vol_oi(vol_oi)
    s_prem   = score_premium(premium_flow)
    s_fund   = max(0.0, min(100.0, float(fundamental_score)))

    composite = (
        s_exp  * (WEIGHT_EXPIRATION  / 100) +
        s_iv   * (WEIGHT_IV_RANK     / 100) +
        s_cp   * (WEIGHT_CALL_PUT    / 100) +
        s_voi  * (WEIGHT_VOL_OI      / 100) +
        s_prem * (WEIGHT_PREMIUM     / 100) +
        s_fund * (WEIGHT_FUNDAMENTAL / 100)
    )

    alert_level = (
        "red"    if composite >= THRESHOLD_RED    else
        "orange" if composite >= THRESHOLD_ORANGE else
        "green"
    )

    return {
        "vol_oi_ratio":    round(vol_oi, 3),
        "iv_rank":         round(iv_rank, 1),
        "call_put_ratio":  round(call_put_ratio, 2),
        "composite_score": round(composite, 1),
        "alert_level":     alert_level,
        "component_scores": {
            "expiration_score":   round(s_exp, 1),
            "iv_rank_score":      round(s_iv, 1),
            "call_put_score":     round(s_cp, 1),
            "vol_oi_score":       round(s_voi, 1),
            "premium_score":      round(s_prem, 1),
            "fundamental_score":  round(s_fund, 1),
        },
        "weights": {
            "expiration":  WEIGHT_EXPIRATION,
            "iv_rank":     WEIGHT_IV_RANK,
            "call_put":    WEIGHT_CALL_PUT,
            "vol_oi":      WEIGHT_VOL_OI,
            "premium":     WEIGHT_PREMIUM,
            "fundamental": WEIGHT_FUNDAMENTAL,
        },
    }


# ── Main analyzer ─────────────────────────────────────────────────────────────

def analyze_ticker(
    ticker: str,
    polygon_client,
    yfinance_client,
    event_date: Optional[date] = None,
    event_type: Optional[str] = None,
    drug_name: Optional[str] = None,
    company: Optional[str] = None,
    db=None,
    fda_event_id: Optional[int] = None,
) -> Optional[dict]:
    """
    Full signal analysis for one ticker.
    Returns dict ready to store as OptionsSignal (keys match model columns).
    """
    logger.info(f"Analyzing {ticker}...")

    # ── Stock info ────────────────────────────────────────────────────────────
    stock_info = yfinance_client.get_stock_info(ticker)
    stock_price = stock_info.get("price", 0)
    market_cap  = stock_info.get("market_cap", 0)

    if market_cap and market_cap < 50_000_000:
        logger.info(f"Skipping {ticker}: market cap ${market_cap:,.0f} < $50M")
        return None

    # ── IV history ────────────────────────────────────────────────────────────
    iv_history = yfinance_client.get_iv_history(ticker)
    iv_min     = iv_history.get("iv_min", 0)
    iv_max     = iv_history.get("iv_max", 100)
    iv_current = iv_history.get("iv_current", 50)

    # ── Per-expiry options data ───────────────────────────────────────────────
    options_by_expiry = polygon_client.get_options_by_expiry(ticker)

    # ── Expiration analysis ───────────────────────────────────────────────────
    exp_result = {"event_pinned_ratio": 0, "expiration_score": 0,
                  "best_expiry": None, "dominant_strike_type": "unknown",
                  "breakdown": [], "total_weighted_call_vol": 0,
                  "total_weighted_put_vol": 0, "expected_move_pct": None}

    if options_by_expiry and event_date:
        from backend.signals.expiration_analyzer import analyze_expirations
        exp_result = analyze_expirations(options_by_expiry, event_date, stock_price)

    # ── Aggregate options summary (for legacy metrics) ────────────────────────
    total_call_vol = sum(v.get("call_volume", 0) for v in options_by_expiry.values())
    total_put_vol  = sum(v.get("put_volume",  0) for v in options_by_expiry.values())
    total_call_oi  = sum(v.get("call_oi",     0) for v in options_by_expiry.values())
    total_put_oi   = sum(v.get("put_oi",      0) for v in options_by_expiry.values())
    total_volume   = total_call_vol + total_put_vol
    total_oi       = total_call_oi  + total_put_oi
    premium_flow   = sum(v.get("premium_flow", 0) for v in options_by_expiry.values())
    iv_vals        = [v.get("avg_call_iv", 0) for v in options_by_expiry.values() if v.get("avg_call_iv")]
    implied_vol    = sum(iv_vals) / len(iv_vals) if iv_vals else iv_current

    # Fallback when no options data
    if not options_by_expiry:
        implied_vol  = iv_current
        total_volume = 0
        total_oi     = 1
        premium_flow = 0

    # ── Fundamental analysis ──────────────────────────────────────────────────
    fund_result = {"fundamental_score": 50.0, "fundamental_flags": {}, "fundamental_detail": {}}
    try:
        from backend.signals.fundamental_analyzer import analyze_fundamentals
        fund_result = analyze_fundamentals(
            ticker,
            event_type=event_type,
            drug_name=drug_name,
            company=company,
        )
    except Exception as e:
        logger.debug(f"Fundamental analysis failed for {ticker}: {e}")

    # ── Composite score ───────────────────────────────────────────────────────
    scores = compute_composite_score(
        call_volume=total_call_vol,
        put_volume=total_put_vol,
        total_volume=total_volume,
        open_interest=max(total_oi, 1),
        implied_volatility=implied_vol,
        iv_min=iv_min,
        iv_max=iv_max,
        premium_flow=premium_flow,
        expiration_score=exp_result["expiration_score"],
        fundamental_score=fund_result["fundamental_score"],
    )

    # ── Probability calibration ───────────────────────────────────────────────
    prob = {"p_up_5": None, "p_up_10": None, "p_down_5": None, "p_down_10": None,
            "p_calibration_n": 0, "p_confidence": "low"}
    try:
        from backend.signals.probability import compute_probability
        prob = compute_probability(
            composite_score=scores["composite_score"],
            event_type=event_type,
            event_pinned_ratio=exp_result["event_pinned_ratio"],
            db=db,
        )
    except Exception as e:
        logger.debug(f"Probability compute error for {ticker}: {e}")

    # ── A2: Entry timing window ───────────────────────────────────────────────
    days_until = (event_date - date.today()).days if event_date else 999
    if days_until >= 15:
        entry_window = "early"
    elif days_until >= 7:
        entry_window = "optimal"
    elif days_until >= 3:
        entry_window = "late"
    else:
        entry_window = "avoid"

    # ── A3: Liquidity gate + IV crush warning ─────────────────────────────────
    liquidity_warning = bool(total_volume < 100 or total_oi < 500)
    iv_crush_warning  = bool(scores["iv_rank"] > 75 and days_until <= 3)

    # ── A4: Earnings overlap ──────────────────────────────────────────────────
    earnings_overlap = False
    if event_date:
        try:
            earnings_date = yfinance_client.get_earnings_date(ticker)
            if earnings_date:
                gap = abs((earnings_date - event_date).days)
                earnings_overlap = gap <= 5
        except Exception:
            pass

    # ── A5: Multi-day flow velocity ───────────────────────────────────────────
    flow_velocity = 0.0
    if db:
        try:
            from backend.models import OptionsSignal as _OS
            recent = (
                db.query(_OS)
                .filter(_OS.ticker == ticker)
                .order_by(_OS.scan_time.desc())
                .limit(3)
                .all()
            )
            if len(recent) >= 1:
                avg_hist = sum(r.premium_flow or 0 for r in recent) / len(recent)
                if avg_hist > 0:
                    flow_velocity = round((premium_flow - avg_hist) / avg_hist * 100, 1)
        except Exception as e:
            logger.debug(f"Flow velocity error for {ticker}: {e}")

    # ── Stock signal (0-7 day window for stock trading) ──────────────────────
    from datetime import timedelta
    stock_signal = "WATCH"
    stock_signal_reason = ""
    entry_price_val = stock_price if stock_price else None
    stop_loss_price_val = round(stock_price * 0.92, 2) if stock_price else None
    target_date_val = (event_date - timedelta(days=1)).isoformat() if event_date else None

    if 0 <= days_until <= 7:
        score_val = scores["composite_score"]
        cp_val    = scores["call_put_ratio"]
        fv_val    = flow_velocity

        # High C/P override: exceptional call flow overrides score threshold
        high_cp_override = cp_val >= 5.0 and score_val >= 40
        # Standard BUY: score≥50 + bullish flow (lowered from 55 — Polygon 403 depresses scores)
        standard_buy = score_val >= 50 and cp_val >= 1.8

        if (standard_buy or high_cp_override) and not liquidity_warning:
            stock_signal = "BUY"
            reasons = []
            if high_cp_override and not standard_buy:
                reasons.append(f"exceptional call flow (C/P {cp_val:.1f})")
            elif score_val >= 65:
                reasons.append(f"strong signal ({score_val:.0f})")
            else:
                reasons.append(f"signal score {score_val:.0f}")
            if cp_val >= 2.5:
                reasons.append(f"bullish C/P {cp_val:.1f}")
            if fv_val > 30:
                reasons.append(f"rising premium (+{fv_val:.0f}%)")
            if exp_result["event_pinned_ratio"] > 0.5:
                reasons.append("event-pinned options")
            stock_signal_reason = " | ".join(reasons)

        elif (standard_buy or high_cp_override) and liquidity_warning:
            # Good signal but thin market — downgrade to WATCH with caution note
            stock_signal = "WATCH"
            stock_signal_reason = f"signal ok (score {score_val:.0f}, C/P {cp_val:.1f}) — low liquidity, size small"

        elif score_val >= 38 and cp_val >= 1.3 and not liquidity_warning:
            stock_signal = "WATCH"
            stock_signal_reason = f"moderate signal — wait for stronger flow (score {score_val:.0f})"

        elif liquidity_warning and score_val < 40:
            stock_signal = "AVOID"
            stock_signal_reason = "low liquidity + weak signal"

        elif iv_crush_warning and score_val < 40:
            stock_signal = "AVOID"
            stock_signal_reason = "IV overpriced with weak directional signal"
    else:
        # Outside 0-7 day window — monitoring only
        stock_signal = "WATCH"
        stock_signal_reason = f"monitoring — {days_until}d until event (enter at 0-7d)"
        entry_price_val = None
        stop_loss_price_val = None

    # ── B1: Trade recommendation ──────────────────────────────────────────────
    signal_snapshot = {
        "composite_score":    scores["composite_score"],
        "call_put_ratio":     scores["call_put_ratio"],
        "event_pinned_ratio": exp_result["event_pinned_ratio"],
        "iv_rank":            scores["iv_rank"],
        "best_expiry":        exp_result["best_expiry"],
    }
    try:
        from backend.signals.trade_recommender import recommend
        rec = recommend(signal_snapshot)
    except Exception as e:
        logger.debug(f"Trade recommend error for {ticker}: {e}")
        rec = {"strategy": "watch", "conviction": "low", "rationale": "Error in recommender"}

    return {
        "ticker":        ticker,
        "fda_event_id":  fda_event_id,
        "scan_time":     datetime.utcnow(),
        # raw
        "call_volume":   total_call_vol,
        "put_volume":    total_put_vol,
        "total_volume":  total_volume,
        "open_interest": total_oi,
        "implied_volatility": implied_vol,
        "iv_rank":       scores["iv_rank"],
        "stock_price":   stock_price,
        "market_cap":    market_cap,
        # computed
        "vol_oi_ratio":   scores["vol_oi_ratio"],
        "call_put_ratio": scores["call_put_ratio"],
        "premium_flow":   premium_flow,
        "composite_score": scores["composite_score"],
        "alert_level":    scores["alert_level"],
        # expiration
        "event_pinned_ratio":    exp_result["event_pinned_ratio"],
        "expiration_score":      exp_result["expiration_score"],
        "best_expiry":           exp_result["best_expiry"],
        "dominant_strike_type":  exp_result["dominant_strike_type"],
        "expiration_breakdown_json": json.dumps(exp_result["breakdown"]),
        "expected_move_pct":     exp_result.get("expected_move_pct"),
        # probability
        "p_up_5":          prob.get("p_up_5"),
        "p_up_10":         prob.get("p_up_10"),
        "p_down_5":        prob.get("p_down_5"),
        "p_down_10":       prob.get("p_down_10"),
        "p_calibration_n": prob.get("p_calibration_n", 0),
        "p_confidence":    prob.get("p_confidence", "low"),
        # A2-A5 additions
        "entry_window":       entry_window,
        "liquidity_warning":  int(liquidity_warning),
        "iv_crush_warning":   int(iv_crush_warning),
        "earnings_overlap":   int(earnings_overlap),
        "flow_velocity":      flow_velocity,
        # B1 additions
        "recommended_strategy": rec.get("strategy"),
        "strategy_rationale":   rec.get("rationale"),
        "strategy_conviction":  rec.get("conviction"),
        # Stock signal
        "stock_signal":         stock_signal,
        "stock_signal_reason":  stock_signal_reason,
        "entry_price":          entry_price_val,
        "stop_loss_price":      stop_loss_price_val,
        "target_date":          target_date_val,
        # fundamental
        "fundamental_score":  fund_result["fundamental_score"],
        "cash_warning":       int(fund_result["fundamental_flags"].get("cash_warning", False)),
        "squeeze_setup":      int(fund_result["fundamental_flags"].get("squeeze_setup", False)),
        "analyst_bullish":    int(fund_result["fundamental_flags"].get("analyst_bullish", False)),
        "clinical_score":     (fund_result.get("fundamental_detail") or {}).get("clinical_score"),
        "trial_risk":         int(fund_result["fundamental_flags"].get("trial_risk", False)),
        "strong_trial":       int(fund_result["fundamental_flags"].get("strong_trial", False)),
        # internal (not stored)
        "_component_scores":  scores["component_scores"],
        "_weights":           scores["weights"],
        "_rec_contract":      rec.get("contract"),
        "_rec_exit":          rec.get("exit"),
        "_fundamental_detail": fund_result["fundamental_detail"],
    }
