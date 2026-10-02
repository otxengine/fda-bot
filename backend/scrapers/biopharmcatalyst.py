"""
BiopharmCatalyst FDA Calendar scraper — authenticated API v1.

Uses BPC's official API (key=BPC_API_KEY env var) which provides:
  - /api/user/v1/fda-calendar      — ALL upcoming catalysts (no limit)
  - /api/user/v1/pdufa-calendar    — PDUFA dates + advisory committee dates
  - /api/user/v1/historical-catalysts — past events for learning

Falls back to public calendar (10 events) if no API key is set.

Rate limit: 100 requests / 24h per endpoint — we call each endpoint once per
2-hour scrape cycle, well within limits.

Fields from authenticated API:
  catalyst, catalyst_date, company_name, company_ticker, company_cik,
  company_exchange, drug, indication, stage, pdufa_date, nct_number,
  historical_pop (P(progression)), historical_loa (P(approval)), statuses
"""
import logging
import os
from datetime import date, datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Auth ──────────────────────────────────────────────────────────────────────
BPC_API_KEY  = os.getenv("BPC_API_KEY", "")       # official API key (v1)
BPC_SESSION  = os.getenv("BPC_SESSION", "")        # fallback: laravel_session cookie
BPC_XSRF     = os.getenv("BPC_XSRF_TOKEN", "")    # fallback: XSRF-TOKEN cookie

# ── Endpoints ─────────────────────────────────────────────────────────────────
BASE_V1      = "https://www.biopharmcatalyst.com/api/user/v1"
PUBLIC_API   = "https://www.biopharmcatalyst.com/api/fda-calendar"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.biopharmcatalyst.com/",
}

HIGH_VALUE_LABELS = {"PDUFA", "AdCom", "NDA", "BLA", "Phase 3", "Phase 2/3"}


def _auth_params() -> dict:
    return {"key": BPC_API_KEY} if BPC_API_KEY else {}


def _public_cookies() -> dict:
    c = {}
    if BPC_SESSION:
        c["laravel_session"] = BPC_SESSION
    if BPC_XSRF:
        c["XSRF-TOKEN"] = BPC_XSRF
    return c


def _f(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _log_raw_fields(endpoint: str, items: list[dict]) -> None:
    """TEMPORARY diagnostic (2026-10-02): dumps the full raw key set + one
    sample item from each BPC endpoint at INFO level, one-time per process.
    Purpose: confirm which fields each endpoint actually returns beyond what
    _fetch_v1_fda_calendar/_fetch_v1_pdufa_calendar/fetch_bpc_historical
    currently map — found bpc_approval_prob/bpc_prog_prob already captured
    but unused in scoring; checking for anything else worth wiring in
    (e.g. a true outcome/result field on historical-catalysts) before
    building that out. Public biotech catalyst metadata only, nothing
    sensitive. Remove once the audit is done."""
    import json
    if not items:
        logger.info(f"BPC field audit [{endpoint}]: 0 items returned")
        return
    logger.info(f"BPC field audit [{endpoint}]: keys={sorted(items[0].keys())}")
    logger.info(f"BPC field audit [{endpoint}]: sample={json.dumps(items[0], default=str)[:1500]}")


def _extract_public_items(payload: dict) -> list[dict]:
    """The public calendar endpoint (PUBLIC_API) returns {"data": [...],
    "links": ..., "meta": {"total": ..., "last_page": ...}, ...} — the
    events list is data["data"] directly, NOT data["data"]["data"]. Confirmed
    2026-09-28 against a live response (1098 total events, 25/page, 44
    pages — also fixes the stale "10 events" assumption in the two callers'
    docstrings). The old `data.get("data", {})` then `.get("data", [])`
    assumed one extra layer of nesting that was never actually there — it
    never raised (the isinstance(dict) guard caught the mismatch), it just
    silently returned [] every single time, so every consumer of this
    endpoint (the "enrich with real-time price/volume" step in
    scrape_biopharmcatalyst(), and fetch_bpc_realtime()'s primary path,
    used by the every-10-min already-moving scan) got no data from it."""
    items = payload.get("data")
    return items if isinstance(items, list) else []


def _parse_bpc_date(raw) -> Optional[date]:
    """The public endpoint returns catalyst_date as 'MM/DD/YYYY' (e.g.
    '09/23/2026') — NOT ISO format. date.fromisoformat() on that raises
    ValueError for every single row, which combined with
    _extract_public_items's bug above meant this endpoint never
    contributed a single event even once the nesting was fixed. Falls back
    to ISO parsing too, defensively, in case the API's format ever changes
    or differs by field."""
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(raw)[:10], fmt).date()
        except ValueError:
            continue
    return None


# ── Authenticated API v1 ──────────────────────────────────────────────────────

def _fetch_v1_fda_calendar() -> list[dict]:
    """
    GET /api/user/v1/fda-calendar — all upcoming catalysts.
    Returns events in FdaEvent-compatible format.
    """
    events = []
    today = date.today()
    try:
        resp = requests.get(
            f"{BASE_V1}/fda-calendar",
            params=_auth_params(),
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json()
        if not isinstance(items, list):
            items = items.get("data", [])
        _log_raw_fields("fda-calendar", items)

        for item in items:
            try:
                raw_date = item.get("catalyst_date") or item.get("pdufa_date")
                if not raw_date:
                    continue
                cat_date = date.fromisoformat(str(raw_date)[:10])
            except (ValueError, TypeError):
                continue

            if cat_date < today:
                continue

            ticker = (item.get("company_ticker") or "").strip().upper() or None
            if not ticker:
                continue

            events.append({
                "ticker":     ticker,
                "company":    item.get("company_name", ""),
                "event_type": item.get("stage") or item.get("catalyst") or "Unknown",
                "drug_name":  item.get("drug"),
                "indication": item.get("indication"),
                "event_date": cat_date,
                "source":     "biopharmcatalyst",
                # BPC v1 rich fields
                "bpc_price":        None,   # not in v1 calendar (use realtime endpoint)
                "bpc_change_pct":   None,
                "bpc_rel_volume":   None,
                "bpc_volume":       None,
                "bpc_avg_volume":   None,
                "bpc_optionable":   None,
                "bpc_market_cap":   None,
                "bpc_insider_pct":  None,
                "bpc_float":        None,
                "bpc_price_to_book": None,
                "bpc_approval_prob": _f(item.get("historical_loa")),    # historical likelihood of approval
                "bpc_prog_prob":     _f(item.get("historical_pop")),    # probability of progression
                "bpc_months_cash":  None,
                "bpc_net_cash":     None,
                "bpc_cash_burn":    None,
                "bpc_trial_id":     item.get("nct_number"),
                "bpc_next_label":   item.get("catalyst"),
                "bpc_fda_status":   str(item.get("statuses", "")) if item.get("statuses") else None,
                "_note":            "",
            })

        logger.info(f"BPC v1 fda-calendar: {len(events)} upcoming events")

    except Exception as e:
        logger.error(f"BPC v1 fda-calendar failed: {e}")

    return events


def _fetch_v1_pdufa_calendar() -> list[dict]:
    """
    GET /api/user/v1/pdufa-calendar — PDUFA action dates + AdCom dates.
    These are the HIGHEST conviction events (FDA binary decisions).
    """
    events = []
    today = date.today()
    try:
        resp = requests.get(
            f"{BASE_V1}/pdufa-calendar",
            params=_auth_params(),
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json()
        if not isinstance(items, list):
            items = items.get("data", [])
        _log_raw_fields("pdufa-calendar", items)

        for item in items:
            # Each item has pdufa_date and optionally advisory_committee_date
            for date_field, etype in [
                ("pdufa_date", "PDUFA"),
                ("advisory_committee_date", "AdCom"),
            ]:
                raw_date = item.get(date_field)
                if not raw_date:
                    continue
                try:
                    ev_date = date.fromisoformat(str(raw_date)[:10])
                except (ValueError, TypeError):
                    continue

                if ev_date < today:
                    continue

                ticker = (item.get("company_ticker") or "").strip().upper() or None
                if not ticker:
                    continue

                events.append({
                    "ticker":     ticker,
                    "company":    item.get("company_name", ""),
                    "event_type": etype,
                    "drug_name":  item.get("drug"),
                    "indication": item.get("notes"),
                    "event_date": ev_date,
                    "source":     "biopharmcatalyst",
                    "bpc_trial_id":    None,
                    "bpc_next_label":  f"Priority review: {item.get('priority_review_date')}" if item.get("priority_review_date") else None,
                    "bpc_approval_prob": None,
                    "bpc_prog_prob":     None,
                    "_note":      item.get("notes", ""),
                })

        logger.info(f"BPC v1 pdufa-calendar: {len(events)} PDUFA/AdCom events")

    except Exception as e:
        logger.error(f"BPC v1 pdufa-calendar failed: {e}")

    return events


def fetch_bpc_historical() -> list[dict]:
    """
    GET /api/user/v1/historical-catalysts — past FDA events with outcomes.
    Used by the learning engine to understand which catalysts moved stocks.
    Returns list of historical event dicts.
    """
    if not BPC_API_KEY:
        return []

    results = []
    try:
        resp = requests.get(
            f"{BASE_V1}/historical-catalysts",
            params=_auth_params(),
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json()
        if not isinstance(items, list):
            items = items.get("data", [])
        _log_raw_fields("historical-catalysts", items)

        for item in items:
            results.append({
                "ticker":    (item.get("company_ticker") or "").strip().upper(),
                "company":   item.get("company_name", ""),
                "drug_name": item.get("drug_name"),
                "indication": item.get("indication"),
                "event_date": item.get("date"),
                "event_type": item.get("label") or item.get("catalyst"),
                "nct_number": item.get("nct_number"),
                "press_link": item.get("press_link"),
            })

        logger.info(f"BPC historical catalysts: {len(results)} past events")

    except Exception as e:
        logger.error(f"BPC historical catalysts failed: {e}")

    return results


# ── Public API fallback (10 events, with real-time stock data) ────────────────

def _fetch_public_calendar() -> list[dict]:
    """
    Public BPC calendar — page 1 (25 most-imminent events by catalyst_date;
    the full feed is ~1000+ across ~40 pages, see _extract_public_items),
    with real-time price/volume/fundamental data. Used as fallback (no API
    key) or as enrichment layer for stock data.
    """
    events = []
    today = date.today()
    cookies = _public_cookies()

    try:
        resp = requests.get(
            PUBLIC_API,
            params={"page": 1, "column": "catalyst_date", "direction": "asc"},
            headers=HEADERS,
            cookies=cookies if cookies else None,
            timeout=15,
        )
        resp.raise_for_status()
        items = _extract_public_items(resp.json())
        _log_raw_fields("public-fda-calendar", items)

        for item in items:
            cat_date = _parse_bpc_date(item.get("catalyst_date"))
            if cat_date is None or cat_date < today:
                continue

            ticker = item.get("company_ticker")
            if not ticker:
                companies = item.get("companies") or []
                ticker = companies[0].get("ticker") if companies else None
            if not ticker:
                continue

            events.append({
                "ticker":     ticker,
                "company":    item.get("company_name", ""),
                # "label" doesn't exist on this endpoint's items (confirmed
                # live) — stage_label is the closest match (e.g. "Phase 2a").
                "event_type": item.get("stage_label") or "Unknown",
                "drug_name":  item.get("drug_name") or item.get("name"),
                "indication": item.get("indication"),
                "event_date": cat_date,
                "source":     "biopharmcatalyst",
                # Real-time stock data (only available in public endpoint)
                "bpc_price":        _f(item.get("company_price")),
                "bpc_change_pct":   _f(item.get("company_percent_change")),
                "bpc_change_abs":   _f(item.get("company_change")),
                "bpc_volume":       _f(item.get("volume")),
                "bpc_avg_volume":   _f(item.get("average_daily_volume")),
                "bpc_rel_volume":   _f(item.get("relative_volume")),
                "bpc_optionable":   item.get("company_optionable"),
                "bpc_market_cap":   _f(item.get("market_cap")),
                "bpc_price_to_book": _f(item.get("price_to_book")),
                "bpc_insider_pct":  _f(item.get("insider_holdings_pct")),
                "bpc_float":        _f(item.get("shareinfo_float")),
                "bpc_approval_prob": _f(item.get("likelihood_of_approval")),
                "bpc_months_cash":  _f(item.get("calculated_est_months_cash")),
                "bpc_net_cash":     _f(item.get("calculated_net_cash")),
                "bpc_cash_burn":    _f(item.get("monthly_cash_burn_not_adjusted")),
                "bpc_trial_id":     item.get("clinical_trial_id"),
                "bpc_next_label":   item.get("next_catalyst_label"),
                "bpc_fda_status":   item.get("fda_status_label"),
                "_note":            item.get("note", ""),
            })

    except Exception as e:
        logger.error(f"BPC public calendar failed: {e}")

    return events


# ── Main entry point ──────────────────────────────────────────────────────────

def scrape_biopharmcatalyst(include_all_phases: bool = True) -> list[dict]:
    """
    Fetch upcoming FDA catalysts from BiopharmCatalyst.

    With BPC_API_KEY set (recommended):
      - Calls v1/fda-calendar  → ALL upcoming catalysts (no limit)
      - Calls v1/pdufa-calendar → PDUFA + AdCom dates with extra fields
      - Enriches top-10 with real-time price/volume from public endpoint

    Without API key (fallback):
      - Calls public calendar → 10 most imminent events with real-time stock data
    """
    if BPC_API_KEY:
        # Authenticated: get everything
        v1_events  = _fetch_v1_fda_calendar()
        pdufa_events = _fetch_v1_pdufa_calendar()

        # Merge: pdufa_calendar entries override fda_calendar for PDUFA events
        # (more precise date + AdCom field)
        seen = {}
        for ev in v1_events:
            key = (ev["ticker"], ev["event_date"])
            seen[key] = ev

        for ev in pdufa_events:
            key = (ev["ticker"], ev["event_date"])
            if key not in seen:
                seen[key] = ev
            else:
                # Upgrade event_type to PDUFA if not already
                if seen[key].get("event_type") not in ("PDUFA", "AdCom"):
                    seen[key]["event_type"] = ev["event_type"]

        merged = list(seen.values())

        # Enrich top-10 with real-time price/volume from public API
        try:
            public = _fetch_public_calendar()
            pub_map = {e["ticker"]: e for e in public}
            for ev in merged:
                t = ev["ticker"]
                if t in pub_map:
                    pub = pub_map[t]
                    for field in ("bpc_price","bpc_change_pct","bpc_rel_volume",
                                  "bpc_volume","bpc_avg_volume","bpc_optionable",
                                  "bpc_market_cap","bpc_insider_pct","bpc_float"):
                        if pub.get(field) is not None:
                            ev[field] = pub[field]
        except Exception as e:
            logger.debug(f"Public enrichment failed: {e}")

        logger.info(f"BPC total: {len(merged)} events (authenticated API)")
        return merged

    else:
        # No API key — public endpoint only (10 events with real-time data)
        logger.warning("BPC_API_KEY not set — using public endpoint (10 events only)")
        return _fetch_public_calendar()


def fetch_bpc_realtime() -> list[dict]:
    """
    Lightweight: fetch current price/volume/change for all upcoming BPC events.
    Used by already-moving scan (every 10 min).

    With API key: calls fda-calendar v1 for full event list, enriches with
    public endpoint real-time data for page 1 (25 most-imminent events).
    Without API key: public endpoint only.
    """
    cookies = _public_cookies()
    results = []
    today = date.today()

    try:
        resp = requests.get(
            PUBLIC_API,
            params={"page": 1, "column": "catalyst_date", "direction": "asc"},
            headers=HEADERS,
            cookies=cookies if cookies else None,
            timeout=12,
        )
        resp.raise_for_status()
        items = _extract_public_items(resp.json())

        for item in items:
            ticker = item.get("company_ticker")
            if not ticker:
                continue
            cat_date = _parse_bpc_date(item.get("catalyst_date"))
            if cat_date is None:
                continue

            results.append({
                "ticker":         ticker,
                "company":        item.get("company_name", ""),
                "catalyst_date":  cat_date,
                "event_type":     item.get("stage_label") or "",
                "drug_name":      item.get("drug_name") or item.get("name"),
                "bpc_price":      _f(item.get("company_price")),
                "bpc_change_pct": _f(item.get("company_percent_change")),
                "bpc_rel_volume": _f(item.get("relative_volume")),
                "bpc_volume":     _f(item.get("volume")),
                "bpc_optionable": item.get("company_optionable"),
                "bpc_market_cap": _f(item.get("market_cap")),
            })

        # If API key set, also pull tickers from v1 calendar (fuller list)
        if BPC_API_KEY:
            try:
                v1 = _fetch_v1_fda_calendar()
                existing = {r["ticker"] for r in results}
                for ev in v1:
                    if ev["ticker"] not in existing:
                        results.append({
                            "ticker":         ev["ticker"],
                            "company":        ev.get("company", ""),
                            "catalyst_date":  ev["event_date"],
                            "event_type":     ev.get("event_type", ""),
                            "drug_name":      ev.get("drug_name"),
                            "bpc_price":      None,
                            "bpc_change_pct": None,
                            "bpc_rel_volume": None,
                            "bpc_volume":     None,
                            "bpc_optionable": None,
                            "bpc_market_cap": None,
                        })
            except Exception as e:
                logger.debug(f"v1 enrichment in realtime failed: {e}")

    except Exception as e:
        logger.warning(f"BPC realtime fetch failed: {e}")

    return results
