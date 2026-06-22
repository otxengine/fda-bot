"""
Builds historical results: for past FDA events, fetches pre/post stock prices
from yfinance and computes outcome.
"""
import logging
from datetime import date, timedelta
from typing import Optional
import yfinance as yf

logger = logging.getLogger(__name__)


def get_trading_price_on_or_after(ticker: str, target_date: date, window: int = 5) -> Optional[float]:
    """
    Get closing price on target_date or the nearest trading day after it
    (within window days).
    """
    end = target_date + timedelta(days=window + 2)
    try:
        hist = yf.Ticker(ticker).history(start=target_date.isoformat(), end=end.isoformat())
        if hist.empty:
            return None
        return round(float(hist["Close"].iloc[0]), 4)
    except Exception as e:
        logger.debug(f"Price fetch error {ticker} on {target_date}: {e}")
        return None


def get_trading_price_before(ticker: str, target_date: date, window: int = 5) -> Optional[float]:
    """
    Get closing price on the trading day just before target_date.
    """
    start = target_date - timedelta(days=window + 2)
    try:
        hist = yf.Ticker(ticker).history(start=start.isoformat(), end=target_date.isoformat())
        if hist.empty:
            return None
        return round(float(hist["Close"].iloc[-1]), 4)
    except Exception as e:
        logger.debug(f"Price fetch error {ticker} before {target_date}: {e}")
        return None


def pct_change(before: float, after: float) -> Optional[float]:
    if before and after and before > 0:
        return round((after - before) / before * 100, 2)
    return None


def classify_outcome(change_1d: Optional[float]) -> str:
    if change_1d is None:
        return "unknown"
    if change_1d >= 20:
        return "strong_up"
    if change_1d >= 5:
        return "up"
    if change_1d <= -20:
        return "strong_down"
    if change_1d <= -5:
        return "down"
    return "neutral"


def build_historical_result(
    ticker: str,
    company: str,
    event_type: str,
    drug_name: Optional[str],
    event_date: date,
    source: str,
    signal=None,  # OptionsSignal ORM object, if available
) -> dict:
    """
    Build a historical result dict for a past FDA event.
    Fetches price data from yfinance around event_date.
    """
    today = date.today()
    days_since = (today - event_date).days

    price_before = get_trading_price_before(ticker, event_date)
    price_1d = get_trading_price_on_or_after(ticker, event_date + timedelta(days=1)) if days_since >= 1 else None
    price_3d = get_trading_price_on_or_after(ticker, event_date + timedelta(days=3)) if days_since >= 3 else None
    price_7d = get_trading_price_on_or_after(ticker, event_date + timedelta(days=7)) if days_since >= 7 else None

    change_1d = pct_change(price_before, price_1d)
    change_3d = pct_change(price_before, price_3d)
    change_7d = pct_change(price_before, price_7d)
    outcome = classify_outcome(change_1d)

    result = {
        "ticker": ticker,
        "company": company,
        "event_type": event_type,
        "drug_name": drug_name,
        "event_date": event_date,
        "source": source,
        "price_before": price_before,
        "price_1d_after": price_1d,
        "price_3d_after": price_3d,
        "price_7d_after": price_7d,
        "change_1d_pct": change_1d,
        "change_3d_pct": change_3d,
        "change_7d_pct": change_7d,
        "outcome": outcome,
        "pre_event_score": None,
        "pre_event_iv_rank": None,
        "pre_event_call_put_ratio": None,
        "pre_event_vol_oi_ratio": None,
        "pre_event_premium_flow": None,
        "pre_event_alert_level": None,
    }

    # Attach pre-event signal if available
    if signal:
        result.update({
            "pre_event_score": signal.composite_score,
            "pre_event_iv_rank": signal.iv_rank,
            "pre_event_call_put_ratio": signal.call_put_ratio,
            "pre_event_vol_oi_ratio": signal.vol_oi_ratio,
            "pre_event_premium_flow": signal.premium_flow,
            "pre_event_alert_level": signal.alert_level,
        })

    return result


def seed_past_14_days(db):
    """
    Seed historical results for FDA events from the past 14 days.
    Tries BiopharmaWatch for recent past events, falls back to checking
    any past events already in the DB.
    """
    from backend.models import FdaEvent, HistoricalResult, OptionsSignal
    from datetime import datetime

    today = date.today()
    cutoff_start = today - timedelta(days=14)

    # Check DB for past events we already tracked
    past_events = db.query(FdaEvent).filter(
        FdaEvent.event_date >= cutoff_start,
        FdaEvent.event_date < today,
        FdaEvent.ticker.isnot(None),
    ).all()

    added = 0
    for event in past_events:
        # Skip if already have a historical result for this
        existing = db.query(HistoricalResult).filter(
            HistoricalResult.ticker == event.ticker,
            HistoricalResult.event_date == event.event_date,
        ).first()
        if existing:
            continue

        # Get closest signal before the event
        signal = db.query(OptionsSignal).filter(
            OptionsSignal.ticker == event.ticker,
            OptionsSignal.scan_time <= datetime.combine(event.event_date, datetime.min.time()),
        ).order_by(OptionsSignal.scan_time.desc()).first()

        result_data = build_historical_result(
            ticker=event.ticker,
            company=event.company,
            event_type=event.event_type or "Catalyst",
            drug_name=event.drug_name,
            event_date=event.event_date,
            source=event.source,
            signal=signal,
        )

        hist_result = HistoricalResult(**result_data)
        db.add(hist_result)
        added += 1
        logger.info(f"Historical: {event.ticker} {event.event_date} → {result_data['outcome']} "
                    f"(1d: {result_data['change_1d_pct']}%)")

    # Also try to get recent past catalysts from BiopharmaWatch
    try:
        added += _seed_from_biopharmawatch_recent(db, cutoff_start, today)
    except Exception as e:
        logger.warning(f"BiopharmaWatch past seed failed: {e}")

    if added:
        db.commit()
        logger.info(f"Historical seed: added {added} records")
    return added


def seed_demo_history(db) -> int:
    """
    Seed demo historical results using real biotech tickers and real yfinance price data.
    Uses well-known FDA-active biotech stocks with approximate catalyst dates
    inferred from significant price moves in the past 14 days.
    """
    from backend.models import HistoricalResult
    import yfinance as yf
    import pandas as pd

    today = date.today()
    # Well-known FDA-active biotech tickers
    candidates = ["MRNA", "BIIB", "VRTX", "REGN", "SGEN", "NVAX",
                  "SRPT", "NBIX", "SAGE", "ALNY", "BEAM", "CRSP",
                  "EDIT", "NTLA", "INO", "ACHV"]

    added = 0
    for ticker in candidates:
        try:
            hist = yf.Ticker(ticker).history(
                start=(today - timedelta(days=20)).isoformat(),
                end=today.isoformat()
            )
            if hist.empty or len(hist) < 5:
                continue

            # Find the day with biggest single-day move in past 14 days
            hist = hist.tail(14)
            hist["pct"] = hist["Close"].pct_change() * 100
            max_idx = hist["pct"].abs().idxmax()
            if pd.isna(max_idx):
                continue

            move_pct = hist.loc[max_idx, "pct"]
            if abs(move_pct) < 3:
                continue  # no significant move - skip

            event_date_ts = max_idx.date() if hasattr(max_idx, "date") else max_idx
            if event_date_ts >= today:
                continue

            # Check if already in DB
            from backend.models import HistoricalResult
            existing = db.query(HistoricalResult).filter(
                HistoricalResult.ticker == ticker,
                HistoricalResult.event_date == event_date_ts,
            ).first()
            if existing:
                continue

            price_before = get_trading_price_before(ticker, event_date_ts)
            price_1d = get_trading_price_on_or_after(ticker, event_date_ts + timedelta(days=1))
            price_3d = get_trading_price_on_or_after(ticker, event_date_ts + timedelta(days=3))
            price_7d = get_trading_price_on_or_after(ticker, event_date_ts + timedelta(days=7))

            change_1d = pct_change(price_before, price_1d)
            change_3d = pct_change(price_before, price_3d)
            change_7d = pct_change(price_before, price_7d)
            outcome = classify_outcome(change_1d)

            info = yf.Ticker(ticker).info
            company = info.get("longName") or info.get("shortName") or ticker

            db.add(HistoricalResult(
                ticker=ticker,
                company=company,
                event_type="Catalyst (estimated)",
                drug_name=None,
                event_date=event_date_ts,
                source="demo/yfinance",
                price_before=price_before,
                price_1d_after=price_1d,
                price_3d_after=price_3d,
                price_7d_after=price_7d,
                change_1d_pct=change_1d,
                change_3d_pct=change_3d,
                change_7d_pct=change_7d,
                outcome=outcome,
            ))
            added += 1
            logger.info(f"Demo history: {ticker} {event_date_ts} move={move_pct:+.1f}% → {outcome}")

        except Exception as e:
            logger.debug(f"Demo seed skip {ticker}: {e}")
            continue

    if added:
        db.commit()
    logger.info(f"Demo historical seed: added {added} records")
    return added


def _seed_from_biopharmawatch_recent(db, start_date: date, end_date: date) -> int:
    """Try to scrape recently-passed events from BiopharmaWatch."""
    import requests
    from bs4 import BeautifulSoup
    import re
    from backend.models import HistoricalResult
    from datetime import datetime

    # BiopharmaWatch sometimes shows recently-passed events at top of the table
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    r = requests.get("https://biopharmawatch.com/fda-calendar/", headers=headers, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    table = soup.find("table")
    if not table:
        return 0

    added = 0
    rows = table.find_all("tr")[1:]
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        raw0 = cells[0].get_text(strip=True)
        match = re.match(r"^([A-Z]+)(?=[A-Z][a-z])", raw0)
        if not match:
            continue
        ticker = match.group(1)
        company = raw0[len(ticker):].strip()

        raw4 = cells[4].get_text(strip=True)
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", raw4)
        if not date_match:
            continue
        try:
            event_date = date.fromisoformat(date_match.group(1))
        except ValueError:
            continue

        # Only past events within our window
        if not (start_date <= event_date < end_date):
            continue

        existing = db.query(HistoricalResult).filter(
            HistoricalResult.ticker == ticker,
            HistoricalResult.event_date == event_date,
        ).first()
        if existing:
            continue

        event_type = raw4[:date_match.start()].strip() or "Catalyst"
        drug_raw = cells[5].get_text(strip=True) if len(cells) > 5 else ""
        drug = re.split(r"(?<=[a-z])(?=[A-Z])", drug_raw)[0].strip() if drug_raw else None

        result_data = build_historical_result(
            ticker=ticker, company=company, event_type=event_type,
            drug_name=drug, event_date=event_date, source="biopharmawatch",
        )
        db.add(HistoricalResult(**result_data))
        added += 1

    return added
