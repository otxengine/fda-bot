"""
Fundamental analyzer for FDA catalyst stocks.

Pulls key financial metrics from yfinance and scores them 0-100.
The fundamental score is combined with the technical (options flow) score
to produce a more accurate overall signal.

Factors scored:
  cash_runway       30%  — months of cash left (critical for small biotech)
  short_interest    20%  — short % of float (squeeze potential on approval)
  analyst_consensus 20%  — analyst buy/hold/sell rating
  event_type        15%  — PDUFA > Phase 3 > Phase 2 binary impact
  institutional_own 15%  — % held by institutions (stability proxy)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

EVENT_TYPE_SCORES = {
    "pdufa":    100,
    "nda":      100,
    "bla":      100,
    "advisory": 85,
    "phase 3":  75,
    "phase iii":75,
    "phase 2":  50,
    "phase ii": 50,
    "phase 1":  30,
    "phase i":  30,
    "sba":      60,
    "complete response": 80,
}


def _score_cash_runway(total_cash: Optional[float], operating_cf: Optional[float]) -> float:
    """Score cash runway in months. Profitable companies score 100."""
    if total_cash is None:
        return 50.0  # unknown — neutral
    if operating_cf is None or operating_cf >= 0:
        return 90.0  # profitable or unknown burn — good
    # burn rate: negative operating CF means spending
    monthly_burn = abs(operating_cf) / 12
    if monthly_burn == 0:
        return 90.0
    months = total_cash / monthly_burn
    if months >= 24:
        return 100.0
    if months >= 12:
        return 80.0
    if months >= 6:
        return 55.0
    if months >= 3:
        return 25.0
    return 5.0  # <3 months cash — very risky


def _score_short_interest(short_pct: Optional[float]) -> float:
    """Score short interest % of float. High short = squeeze potential."""
    if short_pct is None:
        return 50.0
    pct = short_pct * 100 if short_pct < 1 else short_pct  # normalize to %
    if pct >= 30:
        return 100.0
    if pct >= 20:
        return 85.0
    if pct >= 10:
        return 65.0
    if pct >= 5:
        return 45.0
    return 30.0


def _score_analyst_consensus(rec_mean: Optional[float]) -> float:
    """
    yfinance recommendationMean: 1.0=strong buy, 3.0=hold, 5.0=strong sell
    """
    if rec_mean is None:
        return 50.0
    if rec_mean <= 1.5:
        return 100.0
    if rec_mean <= 2.0:
        return 80.0
    if rec_mean <= 2.5:
        return 65.0
    if rec_mean <= 3.0:
        return 50.0
    if rec_mean <= 4.0:
        return 25.0
    return 10.0


def _score_event_type(event_type: Optional[str]) -> float:
    if not event_type:
        return 50.0
    key = event_type.lower().strip()
    for k, v in EVENT_TYPE_SCORES.items():
        if k in key:
            return float(v)
    return 50.0


def _score_institutional_ownership(inst_pct: Optional[float]) -> float:
    """% held by institutions — higher = more credibility."""
    if inst_pct is None:
        return 50.0
    pct = inst_pct * 100 if inst_pct <= 1 else inst_pct
    if pct >= 70:
        return 90.0
    if pct >= 50:
        return 75.0
    if pct >= 30:
        return 60.0
    if pct >= 10:
        return 45.0
    return 30.0


def analyze_fundamentals(
    ticker: str,
    event_type: Optional[str] = None,
    drug_name: Optional[str] = None,
    company: Optional[str] = None,
    yfinance_client=None,
    **kwargs,
) -> dict:
    """
    Pull and score fundamental data for a ticker.

    Returns:
        fundamental_score   float 0-100
        fundamental_flags   dict  {cash_ok, squeeze_risk, analyst_buy, ...}
        fundamental_detail  dict  raw values for display
    """
    kwargs["drug_name"] = drug_name
    kwargs["company"]   = company
    raw = _fetch_yfinance_fundamentals(ticker)

    s_cash  = _score_cash_runway(raw.get("total_cash"), raw.get("operating_cf"))
    s_short = _score_short_interest(raw.get("short_pct"))
    s_anal  = _score_analyst_consensus(raw.get("rec_mean"))
    s_inst  = _score_institutional_ownership(raw.get("inst_pct"))

    # Deep clinical analysis (ClinicalTrials.gov + OpenFDA)
    clinical = {"clinical_score": 50.0, "clinical_detail": {}}
    try:
        from backend.signals.clinical_analyzer import analyze_clinical
        clinical = analyze_clinical(
            ticker=ticker,
            drug_name=kwargs.get("drug_name"),
            company=kwargs.get("company"),
            event_type=event_type,
        )
    except Exception as e:
        logger.debug(f"Clinical analysis failed for {ticker}: {e}")

    s_clinical = clinical["clinical_score"]

    # Weights: financial 55% + clinical 45%
    score = (
        s_cash     * 0.20 +
        s_short    * 0.15 +
        s_anal     * 0.15 +
        s_inst     * 0.05 +
        s_clinical * 0.45
    )

    clinical_detail = clinical.get("clinical_detail", {})
    stopped_bad = clinical_detail.get("stopped_bad") or False
    has_results = clinical_detail.get("has_results") or False

    flags = {
        "cash_warning":      s_cash < 30,
        "squeeze_setup":     s_short >= 85,
        "analyst_bullish":   s_anal >= 75,
        "trial_risk":        bool(stopped_bad),
        "strong_trial":      bool(has_results),
        "low_institutional": s_inst < 40,
    }

    detail = {
        "cash_months":      _estimate_cash_months(raw.get("total_cash"), raw.get("operating_cf")),
        "short_pct":        raw.get("short_pct"),
        "rec_mean":         raw.get("rec_mean"),
        "inst_pct":         raw.get("inst_pct"),
        "clinical_score":   clinical.get("clinical_score"),
        "clinical_detail":  clinical_detail,
        "component_scores": {
            "cash_runway":    round(s_cash, 1),
            "short_interest": round(s_short, 1),
            "analyst":        round(s_anal, 1),
            "institutional":  round(s_inst, 1),
            "clinical":       round(s_clinical, 1),
        },
    }

    return {
        "fundamental_score": round(score, 1),
        "fundamental_flags": flags,
        "fundamental_detail": detail,
    }


def _estimate_cash_months(total_cash, operating_cf):
    if total_cash is None:
        return None
    if operating_cf is None or operating_cf >= 0:
        return 99  # profitable
    monthly_burn = abs(operating_cf) / 12
    if monthly_burn == 0:
        return 99
    return round(total_cash / monthly_burn, 1)


def _fetch_yfinance_fundamentals(ticker: str) -> dict:
    """Fetch raw fundamentals from yfinance info dict."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        return {
            "total_cash":  info.get("totalCash"),
            "operating_cf": info.get("operatingCashflow"),
            "short_pct":   info.get("shortPercentOfFloat"),
            "rec_mean":    info.get("recommendationMean"),
            "inst_pct":    info.get("institutionsPercentHeld") or info.get("heldPercentInstitutions"),
        }
    except Exception as e:
        logger.debug(f"Fundamentals fetch failed for {ticker}: {e}")
        return {}
