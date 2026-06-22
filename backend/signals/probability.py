"""
Probability calibration engine.

Combines:
1. Literature-based base rates for pharma FDA events (prior)
2. Observed win rates from our HistoricalResult DB (likelihood)
3. Signal-strength adjustment (composite score + event_pinned_ratio)

Output: P(+5%), P(+10%), P(-5%), P(-10%) with confidence levels.

Bayesian blending:
    n_prior   = virtual sample strength of base rates (= 20)
    n_own     = actual historical samples with similar signal profile
    p_blended = (p_base * n_prior + p_observed * n_own) / (n_prior + n_own)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Base Rates (prior) ─────────────────────────────────────────────────────────
# P(outcome) for FDA catalyst events, unadjusted for signal strength.
# Sources: pharma sector research; approximately correct for US biotech.
BASE_RATES = {
    "PDUFA Date": {
        "p_up5": 0.38, "p_up10": 0.24, "p_down5": 0.42, "p_down10": 0.28
    },
    "Phase 3": {
        "p_up5": 0.52, "p_up10": 0.36, "p_down5": 0.32, "p_down10": 0.21
    },
    "Phase 2": {
        "p_up5": 0.48, "p_up10": 0.30, "p_down5": 0.34, "p_down10": 0.22
    },
    "Phase 1": {
        "p_up5": 0.38, "p_up10": 0.22, "p_down5": 0.30, "p_down10": 0.18
    },
    "AdCom": {
        "p_up5": 0.44, "p_up10": 0.29, "p_down5": 0.38, "p_down10": 0.24
    },
    "NDA": {
        "p_up5": 0.40, "p_up10": 0.25, "p_down5": 0.40, "p_down10": 0.26
    },
    "BLA": {
        "p_up5": 0.40, "p_up10": 0.25, "p_down5": 0.40, "p_down10": 0.26
    },
    "default": {
        "p_up5": 0.40, "p_up10": 0.25, "p_down5": 0.36, "p_down10": 0.22
    },
}

N_PRIOR = 20  # virtual sample strength for base rate

# ── Signal multipliers ─────────────────────────────────────────────────────────
# How much composite_score adjusts the base bullish probability.
SCORE_MULTIPLIERS = [
    (80, 100, 1.55, 0.60),  # score 80-100: up×1.55, down×0.60
    (65,  80, 1.30, 0.75),
    (50,  65, 1.10, 0.90),
    (35,  50, 0.90, 1.10),
    ( 0,  35, 0.70, 1.30),
]

# Additional multiplier when event_pinned_ratio is high
PINNED_BOOST = {
    0.70: 1.15,   # >70% event-pinned: +15% on bullish signal
    0.50: 1.07,
    0.00: 1.00,
}


def _get_base(event_type: Optional[str]) -> dict:
    if not event_type:
        return BASE_RATES["default"]
    # Fuzzy match
    et = event_type.lower()
    if "pdufa" in et:
        return BASE_RATES["PDUFA Date"]
    if "phase 3" in et or "phase3" in et:
        return BASE_RATES["Phase 3"]
    if "phase 2" in et or "phase2" in et:
        return BASE_RATES["Phase 2"]
    if "phase 1" in et or "phase1" in et:
        return BASE_RATES["Phase 1"]
    if "adcom" in et or "advisory" in et:
        return BASE_RATES["AdCom"]
    if "nda" in et:
        return BASE_RATES["NDA"]
    if "bla" in et:
        return BASE_RATES["BLA"]
    return BASE_RATES["default"]


def _score_multipliers(composite_score: float) -> tuple[float, float]:
    """Returns (up_mult, down_mult) for a given composite score."""
    for lo, hi, up_m, down_m in SCORE_MULTIPLIERS:
        if lo <= composite_score <= hi:
            return up_m, down_m
    return 1.0, 1.0


def _pinned_boost(event_pinned_ratio: float) -> float:
    for threshold, boost in PINNED_BOOST.items():
        if event_pinned_ratio >= threshold:
            return boost
    return 1.0


def _clamp(val: float, lo=0.01, hi=0.97) -> float:
    return max(lo, min(hi, val))


def _historical_rates(composite_score: float, db) -> tuple[dict, int]:
    """
    Query HistoricalResult for events with similar signal profiles.
    Returns (observed_rates_dict, n_samples).
    """
    try:
        from backend.models import HistoricalResult

        score_lo = max(0, composite_score - 15)
        score_hi = min(100, composite_score + 15)

        records = db.query(HistoricalResult).filter(
            HistoricalResult.pre_event_score >= score_lo,
            HistoricalResult.pre_event_score <= score_hi,
            HistoricalResult.change_1d_pct.isnot(None),
        ).all()

        n = len(records)
        if n == 0:
            return {}, 0

        changes = [r.change_1d_pct for r in records]
        p_up5   = sum(1 for c in changes if c >= 5)  / n
        p_up10  = sum(1 for c in changes if c >= 10) / n
        p_down5 = sum(1 for c in changes if c <= -5) / n
        p_down10= sum(1 for c in changes if c <= -10) / n

        return {
            "p_up5": p_up5,
            "p_up10": p_up10,
            "p_down5": p_down5,
            "p_down10": p_down10,
        }, n

    except Exception as e:
        logger.debug(f"Historical rate lookup failed: {e}")
        return {}, 0


def compute_probability(
    composite_score: float,
    event_type: Optional[str] = None,
    event_pinned_ratio: float = 0.0,
    db=None,
) -> dict:
    """
    Compute calibrated probabilities for a given signal profile.

    Returns
    -------
    dict:
        p_up_5       float  P(stock +5% within 3 trading days)
        p_up_10      float  P(stock +10%)
        p_down_5     float  P(stock -5%)
        p_down_10    float  P(stock -10%)
        p_calibration_n   int    historical sample size used
        p_confidence str   "high" / "medium" / "low"
        p_method     str   "bayesian" / "signal_adjusted"
    """
    base = _get_base(event_type)

    # 1. Adjust base rates by signal score
    up_mult, down_mult = _score_multipliers(composite_score)

    # 2. Adjust by event-pinned ratio (only boosts bullish when signal is high)
    if composite_score >= 50:
        up_mult *= _pinned_boost(event_pinned_ratio)

    p_up5_adj   = _clamp(base["p_up5"]   * up_mult)
    p_up10_adj  = _clamp(base["p_up10"]  * up_mult)
    p_down5_adj = _clamp(base["p_down5"] * down_mult)
    p_down10_adj= _clamp(base["p_down10"]* down_mult)

    # 3. Bayesian blend with historical data (if DB available)
    n_own = 0
    if db is not None:
        hist_rates, n_own = _historical_rates(composite_score, db)
        if n_own > 0:
            w_base = N_PRIOR
            w_own  = n_own
            denom  = w_base + w_own
            p_up5_adj    = _clamp((base["p_up5"]   * w_base + hist_rates["p_up5"]   * w_own) / denom)
            p_up10_adj   = _clamp((base["p_up10"]  * w_base + hist_rates["p_up10"]  * w_own) / denom)
            p_down5_adj  = _clamp((base["p_down5"] * w_base + hist_rates["p_down5"] * w_own) / denom)
            p_down10_adj = _clamp((base["p_down10"]* w_base + hist_rates["p_down10"]* w_own) / denom)

    # Confidence level
    if n_own >= 30:
        confidence = "high"
    elif n_own >= 10:
        confidence = "medium"
    else:
        confidence = "low"

    method = "bayesian" if n_own > 0 else "signal_adjusted"

    return {
        "p_up_5":          round(p_up5_adj,    3),
        "p_up_10":         round(p_up10_adj,   3),
        "p_down_5":        round(p_down5_adj,  3),
        "p_down_10":       round(p_down10_adj, 3),
        "p_calibration_n": n_own,
        "p_confidence":    confidence,
        "p_method":        method,
    }


def get_calibration_stats(db) -> list[dict]:
    """
    Return win-rate table bucketed by score range.
    Used for /api/calibration endpoint.
    """
    try:
        from backend.models import HistoricalResult

        all_records = db.query(HistoricalResult).filter(
            HistoricalResult.pre_event_score.isnot(None),
            HistoricalResult.change_1d_pct.isnot(None),
        ).all()

        buckets = [
            (80, 100, "80-100"),
            (65,  80, "65-80"),
            (50,  65, "50-65"),
            (35,  50, "35-50"),
            ( 0,  35, "0-35"),
        ]

        result = []
        for lo, hi, label in buckets:
            group = [r for r in all_records if lo <= (r.pre_event_score or 0) <= hi]
            n = len(group)
            if n == 0:
                result.append({"range": label, "n": 0})
                continue
            changes = [r.change_1d_pct for r in group]
            result.append({
                "range":      label,
                "n":          n,
                "p_up5":      round(sum(1 for c in changes if c >= 5)  / n, 3),
                "p_up10":     round(sum(1 for c in changes if c >= 10) / n, 3),
                "p_down5":    round(sum(1 for c in changes if c <= -5) / n, 3),
                "p_down10":   round(sum(1 for c in changes if c <= -10)/ n, 3),
                "avg_change": round(sum(changes) / n, 2),
                "median_change": round(sorted(changes)[n // 2], 2),
            })

        return result

    except Exception as e:
        logger.error(f"Calibration stats error: {e}")
        return []
