"""
Trade strategy recommender.
Called at end of analyze_ticker() with the assembled signal data.
"""


def recommend(signal_data: dict) -> dict:
    score = signal_data.get("composite_score") or 0
    cp    = signal_data.get("call_put_ratio") or 1.0
    pin   = signal_data.get("event_pinned_ratio") or 0
    ivr   = signal_data.get("iv_rank") or 50
    best_expiry = signal_data.get("best_expiry") or "nearest expiry"

    if score >= 70 and cp > 2.5 and pin > 0.6:
        return {
            "strategy":  "long_call",
            "conviction": "high",
            "rationale":  "Strong bullish flow concentrated at event expiry",
            "contract":   f"Buy ATM call expiring {best_expiry}",
            "exit":       "Take 50% profit or stop at -50% premium",
        }

    if score >= 70 and cp < 0.8 and pin > 0.5:
        return {
            "strategy":  "long_put",
            "conviction": "high",
            "rationale":  "Elevated put activity signals bearish positioning",
            "contract":   f"Buy ATM put expiring {best_expiry}",
            "exit":       "Take 50% profit or stop at -50% premium",
        }

    if 50 <= score < 70 and ivr > 60 and 0.7 < cp < 1.5:
        return {
            "strategy":  "long_straddle",
            "conviction": "medium",
            "rationale":  "High IV + neutral flow — direction unclear, big move expected",
            "contract":   f"Buy ATM straddle expiring {best_expiry}",
            "exit":       "Close at 25% gain or before IV crush (2d before event)",
        }

    if score >= 55 and cp > 1.8:
        return {
            "strategy":  "long_call",
            "conviction": "medium",
            "rationale":  "Moderate bullish skew with reasonable signal strength",
            "contract":   f"Buy ATM call expiring {best_expiry}",
            "exit":       "Take 50% profit or stop at -50% premium",
        }

    # Extreme bullish flow (cp > 5) even without expiry pin data
    if score >= 48 and cp > 5:
        return {
            "strategy":  "long_call",
            "conviction": "medium",
            "rationale":  f"Extreme bullish flow ({cp:.1f}x calls vs puts) ahead of catalyst",
            "contract":   f"Buy ATM call expiring {best_expiry}",
            "exit":       "Take 50% profit or stop at -50% premium",
        }

    # High IV + decent score → straddle even without pin confirmation
    if score >= 50 and ivr > 70:
        return {
            "strategy":  "long_straddle",
            "conviction": "medium",
            "rationale":  "Elevated IV with catalyst approaching — big move expected",
            "contract":   f"Buy ATM straddle expiring {best_expiry}",
            "exit":       "Close at 25% gain or before IV crush (2d before event)",
        }

    if ivr > 80 and score < 40:
        return {
            "strategy":  "avoid",
            "conviction": "low",
            "rationale":  "IV overpriced with no directional signal — premium trap",
            "contract":   None,
            "exit":       None,
        }

    return {
        "strategy":  "watch",
        "conviction": "low",
        "rationale":  "Insufficient signal strength for trade recommendation",
        "contract":   None,
        "exit":       None,
    }
