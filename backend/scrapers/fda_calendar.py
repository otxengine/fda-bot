"""
FDA calendar scraper.

Scrapes two sources for AdCom / PDUFA events:
  1. FDA.gov advisory committee calendar (HTML)
  2. OpenFDA API for recent NDA/BLA submissions with estimated PDUFA dates

The old RSS feed /rss-feeds/advisory-committees/rss.xml is dead (404).
Primary FDA catalyst data still comes from biopharmcatalyst.py and biopharma.py.
"""
import re
import logging
from datetime import datetime, date, timedelta
from typing import Optional
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

FDA_ADCOM_URL = "https://www.fda.gov/advisory-committees/advisory-committee-calendar"
FDA_API_URL   = "https://api.fda.gov/drug/drugsfda.json"

COMPANY_TICKER_MAP = {
    "pfizer": "PFE", "merck": "MRK", "johnson & johnson": "JNJ",
    "abbvie": "ABBV", "bristol-myers squibb": "BMY", "eli lilly": "LLY",
    "amgen": "AMGN", "gilead": "GILD", "biogen": "BIIB",
    "regeneron": "REGN", "moderna": "MRNA", "biontech": "BNTX",
    "novavax": "NVAX", "vertex": "VRTX", "alnylam": "ALNY",
    "sarepta": "SRPT", "neurocrine": "NBIX", "jazz pharmaceuticals": "JAZZ",
    "sage therapeutics": "SAGE", "inovio": "INO",
    "global blood": "GBT", "blueprint medicines": "BPMC",
    "ultragenyx": "RARE", "ionis": "IONS", "axsome": "AXSM",
    "praxis": "PRAX", "acadia": "ACAD", "beam therapeutics": "BEAM",
    "crispr therapeutics": "CRSP", "intellia": "NTLA",
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

MONTH_MAP = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
    "jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,
    "aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
}


def _guess_ticker(text: str) -> Optional[str]:
    text_lower = text.lower()
    for key, ticker in COMPANY_TICKER_MAP.items():
        if key in text_lower:
            return ticker
    m = re.search(r'\(([A-Z]{2,5})\)', text)
    return m.group(1) if m else None


def _parse_date(text: str) -> Optional[date]:
    text = text.strip()
    for fmt in ["%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y"]:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    m = re.search(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", text)
    if m:
        month = MONTH_MAP.get(m.group(1).lower())
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(2)))
            except ValueError:
                pass
    return None


def _scrape_fda_adcom_page() -> list[dict]:
    """
    Scrape FDA advisory committee calendar page for date mentions.
    The page is JS-rendered so we extract any dates visible in the static HTML.
    """
    events = []
    today = date.today()
    try:
        resp = requests.get(FDA_ADCOM_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        text = BeautifulSoup(resp.text, "html.parser").get_text(" ", strip=True)

        date_pattern = re.compile(
            r"((?:January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+\d{1,2},?\s*202\d)"
        )
        seen = set()
        for m in date_pattern.finditer(text):
            event_date = _parse_date(m.group(1))
            if not event_date or event_date < today or event_date > today + timedelta(days=180):
                continue
            if event_date in seen:
                continue
            seen.add(event_date)
            start = max(0, m.start() - 200)
            context = text[start:m.end() + 200]
            ticker = _guess_ticker(context)
            events.append({
                "ticker":     ticker,
                "company":    "FDA Advisory Committee",
                "event_type": "AdCom",
                "drug_name":  None,
                "indication": None,
                "event_date": event_date,
                "source":     "fda.gov/adcom",
            })
        logger.info(f"FDA AdCom page: {len(events)} upcoming dates")
    except Exception as e:
        logger.warning(f"FDA AdCom page scrape failed: {e}")
    return events


def _scrape_openfda_upcoming() -> list[dict]:
    """
    OpenFDA: find NDA/BLA applications filed recently to estimate upcoming PDUFA dates.
    """
    events = []
    today = date.today()
    try:
        from_date = (today - timedelta(days=120)).strftime("%Y%m%d")
        to_date   = today.strftime("%Y%m%d")
        resp = requests.get(
            FDA_API_URL,
            params={
                "search": f"submissions.submission_type:ORIG+AND+submissions.submission_status_date:[{from_date}+TO+{to_date}]",
                "limit": 100,
                "sort": "submissions.submission_status_date:desc",
            },
            headers=HEADERS,
            timeout=20,
        )
        if not resp.ok:
            return events
        results = resp.json().get("results", [])
        for drug in results:
            sponsor = drug.get("sponsor_name", "")
            ticker  = _guess_ticker(sponsor)
            products = drug.get("products", [{}])
            brand = products[0].get("brand_name", "") if products else ""
            for sub in drug.get("submissions", []):
                if sub.get("submission_type") != "ORIG":
                    continue
                filed_str = sub.get("submission_status_date", "")
                if not filed_str or len(filed_str) < 8:
                    continue
                try:
                    filed = date(int(filed_str[:4]), int(filed_str[4:6]), int(filed_str[6:8]))
                except ValueError:
                    continue
                priority = sub.get("review_priority", "") == "PRIORITY"
                months = 6 if priority else 10
                yr  = filed.year + (filed.month + months - 1) // 12
                mon = (filed.month + months - 1) % 12 + 1
                try:
                    est_pdufa = date(yr, mon, filed.day)
                except ValueError:
                    continue
                if est_pdufa < today or est_pdufa > today + timedelta(days=180):
                    continue
                events.append({
                    "ticker":     ticker,
                    "company":    sponsor,
                    "event_type": "PDUFA (est.)",
                    "drug_name":  brand or None,
                    "indication": None,
                    "event_date": est_pdufa,
                    "source":     "openfda/nda",
                })
                break
        logger.info(f"OpenFDA: {len(events)} estimated PDUFA dates")
    except Exception as e:
        logger.warning(f"OpenFDA PDUFA estimation failed: {e}")
    return events


def scrape_fda_calendar() -> list[dict]:
    """Aggregate FDA events from AdCom page + OpenFDA NDA submissions."""
    events = _scrape_fda_adcom_page() + _scrape_openfda_upcoming()
    logger.info(f"FDA calendar total: {len(events)} events")
    return events
