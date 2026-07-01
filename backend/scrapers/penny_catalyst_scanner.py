"""
Penny/micro-cap biotech catalyst scanner.

Options flow can't detect stocks with price < $3 or market_cap < $30M.
This scanner uses a completely different approach:

  VOLUME SPIKE  — today's volume vs 20-day average (most predictive signal)
  MOMENTUM      — 3-day price change already building
  SHORT SQUEEZE — high short % → squeeze fuel
  FDA PROXIMITY — event in the next 14 days

Triggered for any FdaEvent ticker that fails the options-based approach.
Also accepts tickers discovered via the EDGAR 8-K scanner.
"""
import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Thresholds
MIN_VOLUME_SPIKE  = 2.0    # today volume / 20-day avg (must be at least 2x)
MIN_MOMENTUM_PCT  = 5.0    # 3-day price change > 5% (pre-move building)
MAX_PRICE         = 5.0    # only for stocks < $5 (options-viable excluded)
MIN_PRICE         = 0.005  # ignore sub-penny stocks (untradeable)
MIN_VOLUME_ABS    = 100_000  # at least 100K shares today (avoid dead stocks)
SCORE_BUY_THRESHOLD = 45   # composite score to trigger BUY alert


def _volume_score(spike_ratio: float) -> float:
    """Score 0-100. 2x = 11, 5x = 44, 10x = 100."""
    if spike_ratio < 2:
        return 0.0
    return min(100.0, (spike_ratio - 1) / 9 * 100)


def _momentum_score(change_pct: float) -> float:
    """Score 0-100. 5% = 25, 20% = 100."""
    if change_pct <= 0:
        return 0.0
    return min(100.0, change_pct / 20 * 100)


def _squeeze_score(short_pct: float) -> float:
    """Score 0-100. 15% short = 38, 40% short = 100."""
    return min(100.0, short_pct / 40 * 100)


def _proximity_score(days_until: int) -> float:
    """Score 0-100 based on days to FDA event."""
    if days_until < 0:
        return 0.0
    if days_until <= 1:
        return 90.0
    if days_until <= 3:
        return 100.0
    if days_until <= 7:
        return 75.0
    if days_until <= 14:
        return 40.0
    return 10.0


def _news_score(news_count_3d: int) -> float:
    """Bonus for recent press releases."""
    return min(100.0, news_count_3d * 25)


def scan_penny_ticker(
    ticker: str,
    event_date: Optional[date] = None,
    event_type: Optional[str] = None,
    company: Optional[str] = None,
) -> Optional[dict]:
    """
    Analyze one penny/micro-cap ticker for pre-catalyst volume spike.
    Returns a signal dict if BUY criteria met, else None.
    """
    try:
        import yfinance as yf
        from datetime import datetime

        t = yf.Ticker(ticker)
        info = t.info

        price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
        if not price or price < MIN_PRICE or price > MAX_PRICE:
            return None

        market_cap = info.get("marketCap") or 0

        # Get price history for volume + momentum
        hist = t.history(period="30d")
        if hist.empty or len(hist) < 5:
            return None

        today_vol = hist["Volume"].iloc[-1]
        if today_vol < MIN_VOLUME_ABS:
            return None

        avg_vol_20 = hist["Volume"].iloc[:-1].tail(20).mean()
        if avg_vol_20 <= 0:
            return None

        volume_spike = today_vol / avg_vol_20

        # 3-day momentum
        price_3d_ago = hist["Close"].iloc[-4] if len(hist) >= 4 else hist["Close"].iloc[0]
        momentum_pct = ((price - price_3d_ago) / price_3d_ago * 100) if price_3d_ago > 0 else 0

        # Short interest
        short_pct = (info.get("shortPercentOfFloat") or 0) * 100

        # Recent news count
        try:
            news = t.news or []
            cutoff_ts = (datetime.utcnow() - timedelta(days=3)).timestamp()
            news_3d = sum(1 for n in news if (n.get("providerPublishTime") or 0) > cutoff_ts)
        except Exception:
            news_3d = 0

        # Days to FDA event
        days_until = (event_date - date.today()).days if event_date else 30

        # ── Composite score (weights sum to 100) ──────────────────────────────
        w_vol   = 40   # volume spike is the strongest predictor
        w_mom   = 25   # momentum means move already starting
        w_sqz   = 15   # short squeeze adds fuel
        w_prox  = 15   # event proximity
        w_news  = 5    # news coverage

        s_vol  = _volume_score(volume_spike)
        s_mom  = _momentum_score(momentum_pct)
        s_sqz  = _squeeze_score(short_pct)
        s_prox = _proximity_score(days_until)
        s_news = _news_score(news_3d)

        composite = (
            s_vol  * (w_vol  / 100) +
            s_mom  * (w_mom  / 100) +
            s_sqz  * (w_sqz  / 100) +
            s_prox * (w_prox / 100) +
            s_news * (w_news / 100)
        )

        if composite < SCORE_BUY_THRESHOLD:
            logger.debug(
                f"Penny scan {ticker}: score={composite:.1f} vol_spike={volume_spike:.1f}x "
                f"mom={momentum_pct:.1f}% — below threshold"
            )
            return None

        # ── Build signal ──────────────────────────────────────────────────────
        reasons = []
        if volume_spike >= 5:
            reasons.append(f"נפח ×{volume_spike:.0f} מעל הממוצע")
        elif volume_spike >= 2:
            reasons.append(f"נפח ×{volume_spike:.1f} מעל הממוצע")

        if momentum_pct >= 5:
            reasons.append(f"מומנטום +{momentum_pct:.1f}% ב-3 ימים")

        if short_pct >= 20:
            reasons.append(f"שורט גבוה {short_pct:.0f}% — פוטנציאל סקוויז")

        if days_until <= 3:
            reasons.append(f"FDA בעוד {days_until} ימים בלבד")
        elif days_until <= 7:
            reasons.append(f"FDA בעוד {days_until} ימים")

        if news_3d >= 2:
            reasons.append(f"{news_3d} ידיעות ב-3 ימים")

        # Risk qualifier — penny stocks have high binary risk
        risk_level = "HIGH" if market_cap < 10_000_000 else "MEDIUM"
        position_advice = "גודל פוזיציה קטן בלבד (0.5%-1%)" if risk_level == "HIGH" else "גודל פוזיציה מוגבל (1%-2%)"

        return {
            "ticker":          ticker,
            "company":         company or info.get("longName") or ticker,
            "price":           round(price, 4),
            "market_cap":      market_cap,
            "event_date":      event_date,
            "event_type":      event_type,
            "days_until":      days_until,
            "stock_signal":    "BUY",
            "signal_source":   "penny_catalyst",
            "composite_score": round(composite, 1),
            "volume_spike":    round(volume_spike, 1),
            "volume_today":    int(today_vol),
            "momentum_3d_pct": round(momentum_pct, 1),
            "short_pct":       round(short_pct, 1),
            "news_count_3d":   news_3d,
            "reason":          " | ".join(reasons),
            "risk_level":      risk_level,
            "position_advice": position_advice,
            "component_scores": {
                "volume":    round(s_vol,  1),
                "momentum":  round(s_mom,  1),
                "squeeze":   round(s_sqz,  1),
                "proximity": round(s_prox, 1),
                "news":      round(s_news, 1),
            },
        }

    except Exception as e:
        logger.debug(f"Penny scan error {ticker}: {e}")
        return None


def scan_all_penny_catalysts(db) -> list[dict]:
    """
    Scan all FdaEvent tickers in the next 14 days that look like penny/micro-caps.
    Returns list of BUY signals with volume-based scoring.
    """
    from datetime import date, timedelta
    from backend.models import FdaEvent

    today = date.today()
    cutoff = today + timedelta(days=14)

    events = db.query(FdaEvent).filter(
        FdaEvent.event_date >= today,
        FdaEvent.event_date <= cutoff,
        FdaEvent.ticker.isnot(None),
    ).all()

    if not events:
        return []

    # Deduplicate by ticker
    seen = set()
    unique_events = []
    for e in sorted(events, key=lambda x: x.event_date):
        if e.ticker not in seen:
            seen.add(e.ticker)
            unique_events.append(e)

    logger.info(f"Penny catalyst scan: checking {len(unique_events)} event tickers...")

    results = []
    for event in unique_events:
        sig = scan_penny_ticker(
            ticker=event.ticker,
            event_date=event.event_date,
            event_type=event.event_type,
            company=event.company,
        )
        if sig:
            results.append(sig)

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    logger.info(f"Penny catalyst scan: {len(results)} BUY signals found")
    return results
