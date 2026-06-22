"""
Scraper for BiopharmaWatch FDA catalyst calendar.

Actual cell structure (cells merged with sub-elements):
  [0] Ticker + Company name  (e.g. "BIIBBiogen Inc.")
  [1] Price + Daily%         (e.g. "196.58-1.05%")
  [2] 30-day trend %
  [3] Market cap             (e.g. "28.61 B")
  [4] Catalyst type + Date   (e.g. "Phase 2 data readout2026-06-20")
  [5] Drug + Indication
  [6] Stage
  ...
"""
import re
import logging
from datetime import datetime, date
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BIOPHARMAWATCH_URL = "https://biopharmawatch.com/fda-calendar/"


def _extract_ticker_company(text: str):
    """
    Split 'BIIBBiogen Inc.' → ('BIIB', 'Biogen Inc.')
    Split point: first uppercase letter followed by a lowercase letter.
    """
    text = text.strip()
    match = re.match(r"^([A-Z]+)(?=[A-Z][a-z])", text)
    if match:
        ticker = match.group(1)
        company = text[len(ticker):]
        return ticker, company.strip()
    # fallback: all-caps prefix up to 6 chars
    match = re.match(r"^([A-Z]{2,6})\s+(.+)$", text)
    if match:
        return match.group(1), match.group(2).strip()
    return None, text


def _extract_event_date(text: str):
    """Extract 'PDUFA Date2026-06-20' → ('PDUFA Date', date(2026,6,20))"""
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if date_match:
        event_type = text[:date_match.start()].strip()
        try:
            return event_type, datetime.strptime(date_match.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
    # Try other formats
    for fmt in [r"(\w+ \d{1,2},? \d{4})", r"(\d{1,2}/\d{1,2}/\d{4})"]:
        m = re.search(fmt, text)
        if m:
            for dfmt in ["%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%m/%d/%Y"]:
                try:
                    return text[:m.start()].strip(), datetime.strptime(m.group(1), dfmt).date()
                except ValueError:
                    pass
    return text.strip(), None


def scrape_biopharmawatch() -> list[dict]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    events = []
    try:
        response = requests.get(BIOPHARMAWATCH_URL, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")

        table = soup.find("table")
        if not table:
            logger.warning("BiopharmaWatch: no table found")
            return []

        rows = table.find_all("tr")[1:]  # skip header
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            ticker, company  = _extract_ticker_company(cells[0].get_text(strip=True))
            event_type, event_date = _extract_event_date(cells[4].get_text(strip=True))
            drug_raw = cells[5].get_text(strip=True) if len(cells) > 5 else ""

            # drug_raw is like "felzartamabAntibody-mediated rejection..." — take first word chunk
            drug = re.split(r"(?<=[a-z])(?=[A-Z])", drug_raw)[0].strip() if drug_raw else None

            if not event_date or event_date < date.today():
                continue
            if not ticker or len(ticker) > 6:
                ticker = None

            events.append({
                "ticker":     ticker,
                "company":    company or "Unknown",
                "event_type": event_type or "Catalyst",
                "drug_name":  drug,
                "indication": None,
                "event_date": event_date,
                "source":     "biopharmawatch",
            })

        logger.info(f"BiopharmaWatch scraper: found {len(events)} upcoming events")

    except Exception as e:
        logger.error(f"Error scraping BiopharmaWatch: {e}")

    return events
