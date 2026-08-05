"""
Trade strategy recommender v2 (2026-08-05).
Called at end of analyze_ticker() with the assembled signal data.

Changes vs v1:
- Binary events (PDUFA/NDA/BLA/AdCom ≤3d away) → prefer straddle unless C/P very strong
- Suspicious C/P >8 → downgrade conviction, add thin-market warning
- Bearish flow (C/P <0.8) with score ≥60 → long_put
- C/P threshold for long_call raised from 1.8 → 2.0 (lesson: avg loss C/P=1.52)
"""


def recommend(signal_data: dict) -> dict:
    score       = signal_data.get("composite_score") or 0
    cp          = signal_data.get("call_put_ratio") or 1.0
    pin         = signal_data.get("event_pinned_ratio") or 0
    ivr         = signal_data.get("iv_rank") or 50
    best_expiry = signal_data.get("best_expiry") or "nearest expiry"
    binary_risk = signal_data.get("binary_event_risk", False)
    suspicious_cp = signal_data.get("suspicious_high_cp", False)  # C/P > 8 = thin market
    event_type  = (signal_data.get("event_type") or "").lower()
    days_until  = signal_data.get("days_until", 999)

    # Detect binary approval events (PDUFA, NDA, BLA, AdCom)
    is_binary_approval = any(t in event_type for t in ["pdufa", "nda", "bla", "adcom"])

    # ── Suspicious C/P (>8): thin-market inflation, not institutional conviction ──
    # Lesson 2026-07-16: OABI C/P=11.25 → -9.2%, RLMD C/P=23 → -4.6%
    # If C/P is suspicious, cap conviction at medium and require high score
    if suspicious_cp:
        if score >= 65 and cp <= 15:
            return {
                "strategy":   "long_call",
                "conviction": "medium",
                "rationale":  f"Bullish flow but C/P={cp:.0f} may reflect thin market — reduce size",
                "contract":   f"Buy ATM call expiring {best_expiry} (half size)",
                "exit":       "Take 30% profit or stop at -40% premium (tighter due to thin market)",
            }
        return {
            "strategy":   "watch",
            "conviction": "low",
            "rationale":  f"C/P={cp:.0f} is likely thin-market distortion, not institutional flow",
            "contract":   None,
            "exit":       None,
        }

    # ── Binary approval events ≤3 days: prefer straddle unless C/P is decisive ──
    # Win rate is ~48% directionally — binary events cut both ways.
    # Only go directional if C/P is very strong (>3.5 bullish or <0.6 bearish).
    if is_binary_approval and days_until <= 3:
        if cp > 3.5 and score >= 65:
            pass  # fall through to directional logic below
        elif cp < 0.6 and score >= 60:
            pass  # fall through to put logic below
        elif score >= 48 and ivr >= 45:
            return {
                "strategy":   "long_straddle",
                "conviction": "medium",
                "rationale":  f"Binary {event_type.upper()} in {days_until}d — direction unclear, straddle captures both sides",
                "contract":   f"Buy ATM straddle expiring {best_expiry}",
                "exit":       "Close at 30% gain or hold through event — exit within 1d of announcement",
            }

    # ── Binary event risk (small cap <$100M) ──────────────────────────────────
    if binary_risk and score >= 50 and ivr >= 40:
        return {
            "strategy":   "long_straddle",
            "conviction": "medium",
            "rationale":  f"Small-cap binary event — asymmetric move expected, direction unknowable",
            "contract":   f"Buy ATM straddle expiring {best_expiry}",
            "exit":       "Close at 30% gain or hold through event announcement",
        }

    # ── High-conviction directional signals ───────────────────────────────────
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

    # ── Medium bullish (raised C/P threshold 1.8→2.0 — lesson: avg loss C/P=1.52) ──
    if score >= 55 and cp > 2.0:
        return {
            "strategy":  "long_call",
            "conviction": "medium",
            "rationale":  "Moderate bullish skew with reasonable signal strength",
            "contract":   f"Buy ATM call expiring {best_expiry}",
            "exit":       "Take 50% profit or stop at -50% premium",
        }

    # ── Medium bearish ────────────────────────────────────────────────────────
    if score >= 55 and cp < 0.8:
        return {
            "strategy":  "long_put",
            "conviction": "medium",
            "rationale":  f"Bearish flow (C/P={cp:.2f}) with catalyst approaching",
            "contract":   f"Buy ATM put expiring {best_expiry}",
            "exit":       "Take 50% profit or stop at -50% premium",
        }

    # ── Extreme bullish flow (cp > 5, verified range 5-8) ────────────────────
    if score >= 48 and 5.0 < cp <= 8.0:
        return {
            "strategy":  "long_call",
            "conviction": "medium",
            "rationale":  f"Extreme bullish flow ({cp:.1f}x calls vs puts) ahead of catalyst",
            "contract":   f"Buy ATM call expiring {best_expiry}",
            "exit":       "Take 50% profit or stop at -50% premium",
        }

    # ── Neutral high IV + decent score → straddle ────────────────────────────
    if score >= 50 and ivr > 60 and 0.7 < cp < 2.0:
        return {
            "strategy":  "long_straddle",
            "conviction": "medium",
            "rationale":  "High IV + neutral flow — direction unclear, big move expected",
            "contract":   f"Buy ATM straddle expiring {best_expiry}",
            "exit":       "Close at 25% gain or before IV crush (2d before event)",
        }

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
