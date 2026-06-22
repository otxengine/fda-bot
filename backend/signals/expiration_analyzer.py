"""
Expiration-aware options analyzer.

Core insight: options bought on expirations CLOSE to the FDA event date
are much more informative than far-dated options. This module weights
volume by proximity to event and produces an expiration concentration score.

Proximity weights:
  expiry < event_date  → weight 0   (expired before event — irrelevant)
  0-7 days after       → weight 3.0 (event-pinned — strongest signal)
  8-14 days after      → weight 2.0
  15-30 days after     → weight 1.5
  31-60 days after     → weight 1.0
  60+ days after       → weight 0.5
"""

from datetime import date
from typing import Optional
import logging
import math

logger = logging.getLogger(__name__)

PROXIMITY_WEIGHTS = [
    (0,   7,  3.0),   # event-pinned
    (8,   14, 2.0),
    (15,  30, 1.5),
    (31,  60, 1.0),
    (61, 999, 0.5),
]


def _proximity_weight(expiry: date, event_date: date) -> float:
    if expiry < event_date:
        return 0.0
    gap = (expiry - event_date).days
    for lo, hi, w in PROXIMITY_WEIGHTS:
        if lo <= gap <= hi:
            return w
    return 0.3


def _classify_moneyness(strike: float, current_price: float) -> str:
    if current_price <= 0:
        return "unknown"
    ratio = strike / current_price
    if ratio < 0.90:
        return "itm"
    if ratio <= 1.05:
        return "atm"
    if ratio <= 1.20:
        return "otm"
    return "deep_otm"


def analyze_expirations(
    options_by_expiry: dict,
    event_date: date,
    current_price: float,
) -> dict:
    """
    Analyze per-expiry options data relative to FDA event date.

    Parameters
    ----------
    options_by_expiry : dict
        {date_str: {call_volume, put_volume, call_oi, put_oi,
                    premium_flow, avg_call_iv, strikes}}
    event_date : date
    current_price : float

    Returns
    -------
    dict with:
        event_pinned_ratio  float 0-1
        expiration_score    float 0-100
        best_expiry         str | None
        dominant_strike_type str
        breakdown           list[dict]
        total_weighted_call_vol float
        total_weighted_put_vol  float
    """
    if not options_by_expiry:
        return _empty_result()

    breakdown = []
    total_w_call = 0.0
    total_w_put = 0.0
    event_w_call = 0.0   # weighted call vol in event-proximal expiries (≤14d)
    best_expiry = None
    best_w_call = -1.0

    for expiry_str, data in options_by_expiry.items():
        try:
            exp_date = date.fromisoformat(expiry_str)
        except ValueError:
            continue

        weight = _proximity_weight(exp_date, event_date)
        if weight == 0:
            continue

        call_vol = data.get("call_volume", 0) or 0
        put_vol  = data.get("put_volume", 0) or 0
        w_call   = call_vol * weight
        w_put    = put_vol * weight
        days_gap = (exp_date - event_date).days

        total_w_call += w_call
        total_w_put  += w_put

        if weight >= 2.0:  # event-proximal: 0-14 days
            event_w_call += w_call

        if w_call > best_w_call:
            best_w_call = w_call
            best_expiry = expiry_str

        # Dominant strike type for this expiry
        strikes = data.get("strikes", [])
        strike_types = [_classify_moneyness(s["strike"], current_price)
                        for s in strikes if s.get("type") == "call" and s.get("volume", 0) > 0]
        dominant = _most_common(strike_types) if strike_types else "unknown"

        breakdown.append({
            "expiry": expiry_str,
            "days_to_event": days_gap,
            "proximity_weight": weight,
            "call_volume": call_vol,
            "put_volume": put_vol,
            "weighted_call_vol": round(w_call, 0),
            "call_put_ratio": round(call_vol / put_vol, 2) if put_vol > 0 else None,
            "dominant_strike_type": dominant,
            "premium_flow": data.get("premium_flow", 0),
        })

    breakdown.sort(key=lambda x: x["days_to_event"])

    total_w_vol = total_w_call + total_w_put
    event_pinned_ratio = (
        event_w_call / total_w_call if total_w_call > 0 else 0.0
    )
    event_pinned_ratio = min(1.0, event_pinned_ratio)

    # Expiration score 0-100
    # 60% from event_pinned_ratio, 40% from overall call dominance in event window
    call_dom = total_w_call / total_w_vol if total_w_vol > 0 else 0.5
    # call_dom 0.5 = neutral, 1.0 = all calls
    call_dom_score = max(0.0, (call_dom - 0.5) / 0.5) * 100  # 0-100

    expiration_score = min(100.0, event_pinned_ratio * 60 + call_dom_score * 0.40)

    # Overall dominant strike type across event-proximal expiries
    all_types = [row["dominant_strike_type"] for row in breakdown
                 if row["proximity_weight"] >= 2.0 and row["dominant_strike_type"] != "unknown"]
    dominant_overall = _most_common(all_types) if all_types else "unknown"

    # A1: Expected stock move until FDA event date (IV × √(days_to_event/365))
    expected_move_pct = None
    if best_expiry and best_expiry in options_by_expiry:
        best_data = options_by_expiry[best_expiry]
        avg_iv = best_data.get("avg_call_iv") or 0  # annualized IV in %
        if avg_iv > 0:
            try:
                dte = max(1, (event_date - date.today()).days)
                expected_move_pct = round(avg_iv * math.sqrt(dte / 365), 1)
            except Exception:
                pass

    return {
        "event_pinned_ratio": round(event_pinned_ratio, 3),
        "expiration_score": round(expiration_score, 1),
        "best_expiry": best_expiry,
        "dominant_strike_type": dominant_overall,
        "breakdown": breakdown,
        "total_weighted_call_vol": round(total_w_call, 0),
        "total_weighted_put_vol": round(total_w_put, 0),
        "expected_move_pct": expected_move_pct,
    }


def _empty_result() -> dict:
    return {
        "event_pinned_ratio": 0.0,
        "expiration_score": 0.0,
        "best_expiry": None,
        "dominant_strike_type": "unknown",
        "breakdown": [],
        "total_weighted_call_vol": 0.0,
        "total_weighted_put_vol": 0.0,
        "expected_move_pct": None,
    }


def _most_common(lst: list):
    return max(set(lst), key=lst.count) if lst else None
