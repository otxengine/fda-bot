"""
Macro/sector risk-context signals — biotech sector strength (XBI vs SPY) and
a coarse macro risk-off flag (VIX, optionally the 10Y-2Y yield curve).

Why this exists: fda-bot scores every catalyst in isolation today. Small-cap/
pre-revenue biotech is a classic high-duration, risk-on asset class — when
sector money is broadly flowing out, even a good catalyst-specific signal
tends to get sold into; the same C/P ratio and score mean less. This module
adds that context.

Deliberately used as a POSITION-SIZE/CONVICTION overlay in
backend/signals/trade_recommender.py, never as a new weighted input to
compute_composite_score() (backend/signals/analyzer.py) — the composite
score's existing weights are still being recalibrated on a thin, freshly-
cleaned real-catalyst sample (see backend/constants.py's IV_PLACEHOLDER_SOURCE
finding). Bolting a new numeric feature onto that score now would be
overfitting on top of overfitting; a coarse "downgrade conviction" gate is
much harder to overfit and easier to reason about.

Both signals use only what's already a dependency (yfinance — XBI/SPY/^VIX)
so this needs NO new API key and works with zero dependency on any other
service being reachable. The 10Y-2Y yield-curve check is an optional
enhancement, active only if FRED_API_KEY is set (same free key finresearch
uses) — its absence never blocks or degrades the yfinance-based signals.

Both cache in-process with their own TTL so a scan of hundreds of tickers in
one run only fetches these once, not once per ticker.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Thresholds are deliberately simple, round numbers — not fit to any
# backtest — since the whole point is a coarse, hard-to-overfit gate.
SECTOR_STRONG_PCT = 3.0    # XBI outperforming SPY by this much over the lookback -> "strong"
SECTOR_WEAK_PCT   = -3.0   # underperforming by this much -> "weak"
SECTOR_LOOKBACK_DAYS = 21  # ~1 trading month, matches finresearch's sector_rotation.py default

VIX_RISK_OFF = 28.0   # widely-used "elevated fear" threshold
VIX_RISK_ON  = 15.0   # widely-used "complacent/calm" threshold

_SECTOR_CACHE_TTL = 1800   # 30 min — matches market-hours scan cadence
_MACRO_CACHE_TTL  = 21600  # 6h — these series move at most daily

_sector_cache: dict = {"data": None, "fetched_at": 0.0}
_macro_cache: dict = {"data": None, "fetched_at": 0.0}


def get_sector_momentum() -> dict:
    """
    Biotech sector (XBI) relative strength vs SPY over SECTOR_LOOKBACK_DAYS.
    Returns {"label": "strong"/"neutral"/"weak"/"unknown",
             "xbi_rel_strength_pct": float|None, "as_of": iso date|None}
    Never raises — any failure returns label="unknown".
    """
    now = time.monotonic()
    if _sector_cache["data"] is not None and (now - _sector_cache["fetched_at"]) < _SECTOR_CACHE_TTL:
        return _sector_cache["data"]

    result = {"label": "unknown", "xbi_rel_strength_pct": None, "as_of": None}
    try:
        import yfinance as yf

        period = f"{SECTOR_LOOKBACK_DAYS + 10}d"  # padding for weekends/holidays
        xbi = yf.Ticker("XBI").history(period=period)
        spy = yf.Ticker("SPY").history(period=period)
        if len(xbi) < SECTOR_LOOKBACK_DAYS or len(spy) < SECTOR_LOOKBACK_DAYS:
            _sector_cache.update(data=result, fetched_at=now)
            return result

        xbi_ret = (xbi["Close"].iloc[-1] / xbi["Close"].iloc[-SECTOR_LOOKBACK_DAYS] - 1) * 100
        spy_ret = (spy["Close"].iloc[-1] / spy["Close"].iloc[-SECTOR_LOOKBACK_DAYS] - 1) * 100
        rel = round(float(xbi_ret - spy_ret), 2)

        label = "strong" if rel >= SECTOR_STRONG_PCT else "weak" if rel <= SECTOR_WEAK_PCT else "neutral"
        result = {
            "label": label,
            "xbi_rel_strength_pct": rel,
            "as_of": xbi.index[-1].date().isoformat(),
        }
    except Exception as e:
        logger.debug(f"get_sector_momentum failed: {e}")

    _sector_cache.update(data=result, fetched_at=now)
    return result


def get_macro_risk_flag() -> dict:
    """
    Coarse risk-on/neutral/risk-off macro read: VIX level (yfinance, always
    on), optionally deepened by 10Y-2Y yield curve inversion if FRED_API_KEY
    is set. Returns {"label": ..., "vix": float|None, "t10y2y": float|None,
    "as_of": iso date|None}. Never raises — failures return label="unknown".
    """
    now = time.monotonic()
    if _macro_cache["data"] is not None and (now - _macro_cache["fetched_at"]) < _MACRO_CACHE_TTL:
        return _macro_cache["data"]

    result = {"label": "unknown", "vix": None, "t10y2y": None, "as_of": None}
    try:
        import yfinance as yf

        vix_hist = yf.Ticker("^VIX").history(period="5d")
        if not vix_hist.empty:
            vix = float(vix_hist["Close"].iloc[-1])
            as_of = vix_hist.index[-1].date().isoformat()
            label = "risk_off" if vix >= VIX_RISK_OFF else "risk_on" if vix <= VIX_RISK_ON else "neutral"
            result = {"label": label, "vix": round(vix, 1), "t10y2y": None, "as_of": as_of}

            # Optional deepening: an inverted curve alongside an already-elevated
            # VIX is a stronger risk-off read than either alone. Never upgrades
            # risk_on -> risk_off by itself; only sharpens an already-neutral/
            # risk_off VIX read, and only if the key is configured.
            t10y2y = _fetch_fred_t10y2y()
            if t10y2y is not None:
                result["t10y2y"] = t10y2y
                if t10y2y < 0 and label != "risk_on":
                    result["label"] = "risk_off"
    except Exception as e:
        logger.debug(f"get_macro_risk_flag failed: {e}")

    _macro_cache.update(data=result, fetched_at=now)
    return result


def _fetch_fred_t10y2y() -> Optional[float]:
    """Latest 10Y-2Y Treasury spread from FRED. None if FRED_API_KEY isn't
    set or the request fails — this is an optional enhancement, never a
    hard dependency (see module docstring)."""
    api_key = os.getenv("FRED_API_KEY", "")
    if not api_key:
        return None
    try:
        import requests

        resp = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": "T10Y2Y",
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
        if not obs or obs[0]["value"] == ".":
            return None
        return round(float(obs[0]["value"]), 2)
    except Exception as e:
        logger.debug(f"FRED T10Y2Y fetch failed: {e}")
        return None
