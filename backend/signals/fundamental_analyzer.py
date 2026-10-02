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
    "adcom":    85,
    "advisory": 85,
    "phase 3":  75,
    "phase iii":75,
    "phase 2/3":80,
    "phase 2":  50,
    "phase ii": 50,
    "phase 1":  30,
    "phase i":  30,
    "sba":      60,
    "complete response": 80,
}


def _score_cash_runway(
    total_cash: Optional[float],
    operating_cf: Optional[float],
    bpc_months_cash: Optional[float] = None,
) -> float:
    """Score cash runway in months. Profitable companies score 100.

    bpc_months_cash (BiopharmCatalyst's own pre-computed "months of cash"
    estimate, calculated_est_months_cash on its public endpoint) is used
    directly when yfinance didn't give us total_cash — a real fallback, not
    a guess, and valuable right now since yfinance's fundamentals fetch is
    broken in production (Yahoo's crumb/401 wall — see yfinance_client.py)."""
    if total_cash is None:
        if bpc_months_cash is not None:
            return _score_months(bpc_months_cash)
        return 50.0  # unknown — neutral
    if operating_cf is None or operating_cf >= 0:
        return 90.0  # profitable or unknown burn — good
    # burn rate: negative operating CF means spending
    monthly_burn = abs(operating_cf) / 12
    if monthly_burn == 0:
        return 90.0
    months = total_cash / monthly_burn
    return _score_months(months)


def _score_months(months: float) -> float:
    if months >= 24:
        return 100.0
    if months >= 12:
        return 80.0
    if months >= 6:
        return 55.0
    if months >= 3:
        return 25.0
    return 5.0  # <3 months cash — very risky


def _score_approval_odds(
    bpc_approval_prob: Optional[float],
    bpc_prog_prob: Optional[float],
) -> float:
    """Score BiopharmCatalyst's own historical base-rate odds for this
    drug/indication/phase: likelihood_of_approval (LoA) and
    likelihood_of_progressing (PoP), both already fetched and stored on
    FdaEvent (bpc_approval_prob/bpc_prog_prob) but never read by any scoring
    code until now. Both are 0.0-1.0 probabilities — scaled to 0-100 and
    averaged when both are present (LoA weighted higher: it's the more
    direct "will this drug actually get approved" question; PoP answers
    "will the trial even finish/advance", a precondition but one step
    removed). Neutral 50.0 when BCP didn't have data for this ticker."""
    if bpc_approval_prob is None and bpc_prog_prob is None:
        return 50.0
    if bpc_approval_prob is not None and bpc_prog_prob is not None:
        return (bpc_approval_prob * 0.65 + bpc_prog_prob * 0.35) * 100
    return (bpc_approval_prob if bpc_approval_prob is not None else bpc_prog_prob) * 100


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
    bpc_approval_prob: Optional[float] = None,
    bpc_prog_prob: Optional[float] = None,
    bpc_months_cash: Optional[float] = None,
    **kwargs,
) -> dict:
    """
    Pull and score fundamental data for a ticker.

    bpc_approval_prob/bpc_prog_prob/bpc_months_cash come from the matching
    FdaEvent row (BiopharmCatalyst's own historical_loa/historical_pop/
    calculated_est_months_cash) when the caller has one — see analyzer.py's
    analyze_ticker(), which looks it up by fda_event_id. All three are
    genuinely optional: a ticker scored without a known FdaEvent (or one BCP
    had no data for) just gets the yfinance-only behavior this always had.

    Returns:
        fundamental_score   float 0-100
        fundamental_flags   dict  {cash_ok, squeeze_risk, analyst_buy, ...}
        fundamental_detail  dict  raw values for display
    """
    kwargs["drug_name"] = drug_name
    kwargs["company"]   = company
    raw = _fetch_yfinance_fundamentals(ticker)

    s_cash  = _score_cash_runway(raw.get("total_cash"), raw.get("operating_cf"), bpc_months_cash)
    s_short = _score_short_interest(raw.get("short_pct"))
    s_anal  = _score_analyst_consensus(raw.get("rec_mean"))
    s_inst  = _score_institutional_ownership(raw.get("inst_pct"))
    s_bpc   = _score_approval_odds(bpc_approval_prob, bpc_prog_prob)

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

    # Weights: financial 55% + clinical 35% + BCP historical approval odds 10%.
    # Was financial 55%/clinical 45% with no BCP component at all — clinical
    # gave up 10pts to make room for s_bpc rather than touching cash/short/
    # analyst/institutional, since those four are independently-sourced
    # (yfinance) signals this score already relied on, while s_bpc and
    # s_clinical both answer "how likely is this drug to actually work/get
    # approved" from two different angles (BCP: historical base rate for this
    # phase/indication; clinical_analyzer: this specific trial's own design/
    # results) — closest existing weight to trade off against.
    score = (
        s_cash     * 0.20 +
        s_short    * 0.15 +
        s_anal     * 0.15 +
        s_inst     * 0.05 +
        s_clinical * 0.35 +
        s_bpc      * 0.10
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
        "bpc_low_odds":      s_bpc < 30 and (bpc_approval_prob is not None or bpc_prog_prob is not None),
    }

    cash_months = _estimate_cash_months(raw.get("total_cash"), raw.get("operating_cf"))
    detail = {
        "cash_months":       cash_months if cash_months is not None else bpc_months_cash,
        "cash_months_source": "yfinance" if cash_months is not None else ("biopharmcatalyst" if bpc_months_cash is not None else None),
        "short_pct":        raw.get("short_pct"),
        "rec_mean":         raw.get("rec_mean"),
        "inst_pct":         raw.get("inst_pct"),
        "bpc_approval_prob": bpc_approval_prob,
        "bpc_prog_prob":     bpc_prog_prob,
        "clinical_score":   clinical.get("clinical_score"),
        "clinical_detail":  clinical_detail,
        "component_scores": {
            "cash_runway":    round(s_cash, 1),
            "short_interest": round(s_short, 1),
            "analyst":        round(s_anal, 1),
            "institutional":  round(s_inst, 1),
            "clinical":       round(s_clinical, 1),
            "bpc_odds":       round(s_bpc, 1),
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
