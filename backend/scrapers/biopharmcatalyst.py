"""
BiopharmCatalyst FDA Calendar scraper.
API returns the next ~10-50 upcoming FDA catalysts (rolling window).
Rich data: ticker, event_type, catalyst_date, drug_name, indication,
           market_cap, relative_volume, likelihood_of_approval.
"""
import logging
from datetime import date, datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

import os

API_URL = "https://www.biopharmcatalyst.com/api/fda-calendar"
BPC_API_KEY = os.getenv("BPC_API_KEY", "bpc_f84d9b2ef66c6da19397e24a0833f921")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.biopharmcatalyst.com/calendars/fda-calendar",
}

# Only track PDUFA + Phase 3 + AdCom (highest-conviction catalysts)
HIGH_VALUE_LABELS = {"PDUFA", "AdCom", "NDA", "BLA", "Phase 3", "Phase 2/3"}


def scrape_biopharmcatalyst(include_all_phases: bool = True) -> list[dict]:
    """
    Fetch upcoming FDA catalysts from BiopharmCatalyst API.
    Returns list of event dicts compatible with FdaEvent model.
    """
    events = []
    today = date.today()

    try:
        # Fetch all pages
        page = 1
        while True:
            resp = requests.get(
                API_URL,
                params={"page": page, "column": "catalyst_date", "direction": "asc"},
                headers=HEADERS,
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

                # Skip past events
                if cat_date < today:
                    continue

                label = item.get("label", "Unknown")

                # Filter phases if needed
                if not include_all_phases and label not in HIGH_VALUE_LABELS:
                    continue

                # Ticker is a direct field (company_ticker)
                ticker = item.get("company_ticker")
                if not ticker:
                    # fallback: nested companies list
                    companies = item.get("companies") or []
                    ticker = companies[0].get("ticker") if companies else None

                if not ticker:
                    continue

                company_name = item.get("company_name", "")

                events.append({
                    "ticker":      ticker,
                    "company":     company_name,
                    "event_type":  label,
                    "drug_name":   item.get("name"),
                    "indication":  item.get("indication"),
                    "event_date":  cat_date,
                    "source":      "biopharmcatalyst",
                    # extra context (stored as indication if not already set)
                    "_market_cap":    item.get("market_cap"),
                    "_rel_volume":    item.get("relative_volume"),
                    "_approval_prob": item.get("likelihood_of_approval"),
                    "_note":          item.get("note", ""),
                })

            # Pagination
            last_page = inner.get("last_page", 1) if isinstance(inner, dict) else 1
            if page >= last_page:
                break
            page += 1

        logger.info(f"BiopharmCatalyst: fetched {len(events)} upcoming events")

    except Exception as e:
        logger.error(f"BiopharmCatalyst scrape failed: {e}")

    return events
