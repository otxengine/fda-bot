"""
SEC EDGAR 8-K PDUFA date scraper.

Companies must file an 8-K when they receive a PDUFA action date (material event).
This scraper queries EDGAR full-text search for recent 8-K filings mentioning PDUFA,
extracts the actual date from the document, and returns structured events.

Free, no API key required. ~300-600 filings/year.
"""
import logging
import re
import requests
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

EDGAR_EFTS = "https://efts.sec.gov/LATEST/search-index"
EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
HEADERS = {"User-Agent": "Mozilla/5.0 fda-scanner/1.0 research@example.com"}

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

DATE_PATTERNS = [
    # "PDUFA Action Date of September 30, 2026"
    r"PDUFA\s+(?:action\s+)?date\s+(?:of\s+)?([A-Z][a-z]+ \d{1,2}(?:st|nd|rd|th)?,?\s*202\d)",
    # "PDUFA date is October 15, 2026"
    r"PDUFA\s+date\s+(?:is\s+)?([A-Z][a-z]+ \d{1,2}(?:st|nd|rd|th)?,?\s*202\d)",
    # "action date of November 2026" (month only)
    r"(?:PDUFA|action)\s+date[^.]{0,50}?([A-Z][a-z]+\s+202\d)",
    # "by September 30th PDUFA date"
    r"([A-Z][a-z]+ \d{1,2}(?:st|nd|rd|th)?,?\s*202\d)[^.]{0,30}PDUFA\s+date",
    # Generic: month day, year near PDUFA
    r"PDUFA.{0,100}?(\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s*202\d)",
]


def _parse_date(text: str) -> Optional[date]:
    """Parse a date string like 'September 30, 2026' or 'September 30th 2026'."""
    text = re.sub(r"(?<=\d)(st|nd|rd|th)", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    for fmt in ["%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"]:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    # Month + year only → 15th of that month
    m = re.match(r"([A-Z][a-z]+)\s+(202\d)$", text)
    if m:
        month = MONTH_MAP.get(m.group(1).lower())
        if month:
            try:
                return date(int(m.group(2)), month, 15)
            except ValueError:
                pass
    return None


def _extract_pdufa_date(html_text: str) -> Optional[date]:
    """Extract PDUFA date from 8-K filing HTML text."""
    clean = re.sub(r"<[^>]+>", " ", html_text)
    clean = re.sub(r"&[a-z#0-9]+;", " ", clean)
    clean = re.sub(r"\s+", " ", clean)

    for pattern in DATE_PATTERNS:
        m = re.search(pattern, clean, re.I)
        if m:
            parsed = _parse_date(m.group(1))
            if parsed and parsed > date.today():
                return parsed
    return None


def _get_filing_doc_url(doc_id: str, cik: str) -> str:
    """Build the correct EDGAR document URL from doc_id and CIK."""
    parts = doc_id.split(":")
    if len(parts) != 2:
        return ""
    accession = parts[0].replace("-", "")
    filename = parts[1]
    cik_clean = cik.lstrip("0")
    return f"{EDGAR_ARCHIVES}/{cik_clean}/{accession}/{filename}"


def scrape_edgar_pdufa(lookback_days: int = 180) -> list[dict]:
    """
    Search EDGAR for recent 8-K filings mentioning PDUFA and extract event dates.

    Returns list of event dicts compatible with FdaEvent model.
    """
    events = []
    today = date.today()
    start_dt = (today - timedelta(days=lookback_days)).isoformat()

    try:
        all_hits = []
        for from_idx in range(0, 500, 100):
            r = requests.get(
                f'{EDGAR_EFTS}?q=%22PDUFA+Action+Date%22&forms=8-K&from={from_idx}',
                headers=HEADERS,
                timeout=15,
            )
            if not r.ok:
                break
            batch = r.json().get("hits", {}).get("hits", [])
            if not batch:
                break
            all_hits += batch

        hits = all_hits
        logger.info(f"EDGAR: {len(hits)} PDUFA 8-K hits")

        seen_tickers = set()

        for h in hits:
            src = h.get("_source", {})
            doc_id = h.get("_id", "")
            names = src.get("display_names", [])
            ciks = src.get("ciks", [])
            file_date = src.get("file_date", "")

            if not names or not ciks:
                continue

            # Extract ticker
            ticker_m = re.search(r"\(([A-Z]{1,5})\)", names[0])
            if not ticker_m:
                continue
            ticker = ticker_m.group(1)
            if ticker in seen_tickers:
                continue

            # Build company name
            company = re.sub(r"\s*\([A-Z,\s]+\)\s*\(CIK.*", "", names[0]).strip()

            # Fetch the filing document
            doc_url = _get_filing_doc_url(doc_id, ciks[0])
            if not doc_url:
                continue

            try:
                r2 = requests.get(doc_url, headers=HEADERS, timeout=10)
                if not r2.ok:
                    continue

                pdufa_date = _extract_pdufa_date(r2.text)
                if not pdufa_date:
                    continue

                # Only include future events
                if pdufa_date <= today:
                    continue

                seen_tickers.add(ticker)
                events.append({
                    "ticker":     ticker,
                    "company":    company,
                    "event_type": "PDUFA",
                    "drug_name":  None,
                    "indication": None,
                    "event_date": pdufa_date,
                    "source":     "sec_edgar_8k",
                })
                logger.info(f"EDGAR PDUFA: {ticker} → {pdufa_date}")

            except Exception as e:
                logger.debug(f"EDGAR doc fetch {ticker}: {e}")
                continue

    except Exception as e:
        logger.error(f"EDGAR scrape failed: {e}")

    logger.info(f"EDGAR PDUFA scraper: found {len(events)} upcoming PDUFA events")
    return events
