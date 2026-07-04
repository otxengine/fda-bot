"""
BiopharmCatalyst FDA Calendar scraper.

Public API returns the 10 most imminent upcoming catalysts (free tier limit).
If BPC_SESSION env var is set (laravel_session cookie from logged-in browser),
the scraper uses that session to unlock more events (up to 15 for free, unlimited for paid).

Rich data per event (all available on free tier):
  ticker, event_type, catalyst_date, drug_name, indication,
  market_cap, relative_volume, volume, avg_volume,
  company_price, company_change_pct,        ← real-time stock data
  optionable (1/0),                          ← options routing decision
  insider_pct, float_shares, price_to_book,  ← fundamentals
  likelihood_of_approval, months_cash        ← clinical/financial (paid tier)
"""
import logging
import os
from datetime import date
from typing import Optional

import requests

logger = logging.getLogger(__name__)

API_URL = "https://www.biopharmcatalyst.com/api/fda-calendar"
BPC_SESSION = os.getenv("BPC_SESSION", "")       # laravel_session cookie value
BPC_XSRF    = os.getenv("BPC_XSRF_TOKEN", "")   # XSRF-TOKEN cookie value
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.biopharmcatalyst.com/calendars/fda-calendar",
}

HIGH_VALUE_LABELS = {"PDUFA", "AdCom", "NDA", "BLA", "Phase 3", "Phase 2/3"}


def _build_cookies() -> dict:
    cookies = {}
    if BPC_SESSION:
        cookies["laravel_session"] = BPC_SESSION
    if BPC_XSRF:
        cookies["XSRF-TOKEN"] = BPC_XSRF
    return cookies


def scrape_biopharmcatalyst(include_all_phases: bool = True) -> list[dict]:
    """
    Fetch upcoming FDA catalysts from BiopharmCatalyst API.
    Returns list of event dicts compatible with FdaEvent model.
    Captures all rich fields available in the API response.
    """
    events = []
    today = date.today()
    cookies = _build_cookies()

    try:
        page = 1
        while True:
            resp = requests.get(
                API_URL,
                params={"page": page, "column": "catalyst_date", "direction": "asc"},
                headers=HEADERS,
                cookies=cookies if cookies else None,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            inner = data.get("data", {})
            items = inner.get("data", []) if isinstance(inner, dict) else []

            if not items:
                break

            for item in items:
                try:
                    cat_date = date.fromisoformat(item["catalyst_date"])
                except (KeyError, ValueError):
                    continue

                if cat_date < today:
                    continue

                label = item.get("label", "Unknown")
                if not include_all_phases and label not in HIGH_VALUE_LABELS:
                    continue

                ticker = item.get("company_ticker")
                if not ticker:
                    companies = item.get("companies") or []
                    ticker = companies[0].get("ticker") if companies else None
                if not ticker:
                    continue

                # Safe float conversion helper
                def _f(val):
                    try:
                        return float(val) if val is not None else None
                    except (TypeError, ValueError):
                        return None

                events.append({
                    # --- Core event fields ---
                    "ticker":      ticker,
                    "company":     item.get("company_name", ""),
                    "event_type":  label,
                    "drug_name":   item.get("drug_name") or item.get("name"),
                    "indication":  item.get("indication"),
                    "event_date":  cat_date,
                    "source":      "biopharmcatalyst",
                    # --- Real-time stock data (no yfinance needed) ---
                    "bpc_price":        _f(item.get("company_price")),
                    "bpc_change_pct":   _f(item.get("company_percent_change")),
                    "bpc_change_abs":   _f(item.get("company_change")),
                    "bpc_volume":       _f(item.get("volume")),
                    "bpc_avg_volume":   _f(item.get("average_daily_volume")),
                    "bpc_rel_volume":   _f(item.get("relative_volume")),
                    # --- Options routing ---
                    "bpc_optionable":   item.get("company_optionable"),   # 1 or 0
                    # --- Fundamentals ---
                    "bpc_market_cap":   _f(item.get("market_cap")),
                    "bpc_price_to_book": _f(item.get("price_to_book")),
                    "bpc_insider_pct":  _f(item.get("insider_holdings_pct")),
                    "bpc_float":        _f(item.get("shareinfo_float")),
                    # --- Clinical/financial (real values require paid BPC session) ---
                    "bpc_approval_prob": _f(item.get("likelihood_of_approval")),
                    "bpc_months_cash":   _f(item.get("calculated_est_months_cash")),
                    "bpc_net_cash":      _f(item.get("calculated_net_cash")),
                    "bpc_cash_burn":     _f(item.get("monthly_cash_burn_not_adjusted")),
                    # --- Clinical context ---
                    "bpc_trial_id":      item.get("clinical_trial_id"),
                    "bpc_next_label":    item.get("next_catalyst_label"),
                    "bpc_fda_status":    item.get("fda_status_label"),
                    "_note":             item.get("note", ""),
                })

            last_page = inner.get("last_page", 1) if isinstance(inner, dict) else 1
            if page >= last_page:
                break
            page += 1

        logger.info(f"BiopharmCatalyst: fetched {len(events)} upcoming events")

    except Exception as e:
        logger.error(f"BiopharmCatalyst scrape failed: {e}")

    return events


def fetch_bpc_realtime() -> list[dict]:
    """
    Lightweight call: fetches all upcoming BPC events with current price/volume data.
    Used by the already-moving scan (every 10 min) to avoid yfinance calls.
    Returns list of dicts with: ticker, bpc_price, bpc_change_pct, bpc_rel_volume,
                                bpc_volume, bpc_optionable, bpc_market_cap, catalyst_date
    """
    cookies = _build_cookies()
    results = []
    today = date.today()

    try:
        page = 1
        while True:
            resp = requests.get(
                API_URL,
                params={"page": page, "column": "catalyst_date", "direction": "asc"},
                headers=HEADERS,
                cookies=cookies if cookies else None,
                timeout=12,
            )
            resp.raise_for_status()
            data = resp.json()
            inner = data.get("data", {})
            items = inner.get("data", []) if isinstance(inner, dict) else []

            if not items:
                break

            for item in items:
                ticker = item.get("company_ticker")
                if not ticker:
                    continue
                try:
                    cat_date = date.fromisoformat(item["catalyst_date"])
                except (KeyError, ValueError):
                    continue

                def _f(val):
                    try:
                        return float(val) if val is not None else None
                    except (TypeError, ValueError):
                        return None

                results.append({
                    "ticker":         ticker,
                    "company":        item.get("company_name", ""),
                    "catalyst_date":  cat_date,
                    "event_type":     item.get("label", ""),
                    "drug_name":      item.get("drug_name") or item.get("name"),
                    "bpc_price":      _f(item.get("company_price")),
                    "bpc_change_pct": _f(item.get("company_percent_change")),
                    "bpc_rel_volume": _f(item.get("relative_volume")),
                    "bpc_volume":     _f(item.get("volume")),
                    "bpc_optionable": item.get("company_optionable"),
                    "bpc_market_cap": _f(item.get("market_cap")),
                })

            last_page = inner.get("last_page", 1) if isinstance(inner, dict) else 1
            if page >= last_page:
                break
            page += 1

    except Exception as e:
        logger.warning(f"BPC realtime fetch failed: {e}")

    return results
