"""
FDA calendar scraper.
FDA.gov advisory calendar is JS-rendered, so we use their public RSS feed
and the drugs@FDA search page as alternatives.
Primary data source is BiopharmaWatch (handled in biopharma.py).
This module adds AdCom events from FDA RSS if available.
"""
import re
import logging
from datetime import datetime, date
from typing import Optional
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

FDA_RSS_URL = "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/advisory-committees/rss.xml"

COMPANY_TICKER_MAP = {
    "pfizer": "PFE", "merck": "MRK", "johnson & johnson": "JNJ",
    "abbvie": "ABBV", "bristol-myers squibb": "BMY", "eli lilly": "LLY",
    "amgen": "AMGN", "gilead": "GILD", "biogen": "BIIB",
    "regeneron": "REGN", "moderna": "MRNA", "biontech": "BNTX",
    "novavax": "NVAX", "vertex": "VRTX", "alnylam": "ALNY",
    "sarepta": "SRPT", "neurocrine": "NBIX", "jazz pharmaceuticals": "JAZZ",
    "intercept": "ICPT", "seagen": "SGEN", "blueprint medicines": "BPMC",
    "sage therapeutics": "SAGE", "inovio": "INO", "arctus": "RCUS",
    "global blood": "GBT", "turning point": "TPTX", "acceleron": "XLRN",
    "arena pharmaceuticals": "ARNA", "alexion": "ALXN",
}

MONTH_MAP = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
    "jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,
    "aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
}


def guess_ticker(text: str) -> Optional[str]:
    text_lower = text.lower()
    for key, ticker in COMPANY_TICKER_MAP.items():
        if key in text_lower:
            return ticker
    return None


def parse_date_from_text(text: str) -> Optional[date]:
    text = text.strip()
    for fmt in ["%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y"]:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    match = re.search(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", text)
    if match:
        month_str, day_str, year_str = match.groups()
        month = MONTH_MAP.get(month_str.lower())
        if month:
            try:
                return date(int(year_str), month, int(day_str))
            except ValueError:
                pass
    return None


def scrape_fda_calendar() -> list[dict]:
    """
    Scrape FDA advisory committee events from FDA RSS feed.
    Returns list of event dicts (may be empty if RSS yields nothing parseable).
    """
    events = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    try:
        response = requests.get(FDA_RSS_URL, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "xml")

        items = soup.find_all("item")
        logger.info(f"FDA RSS: found {len(items)} items")

        for item in items:
            title = item.find("title")
            pub_date = item.find("pubDate")
            description = item.find("description")

            title_text = title.get_text(strip=True) if title else ""
            desc_text = description.get_text(strip=True) if description else ""
            combined = f"{title_text} {desc_text}"

            # Try to parse event date from title/description
            event_date = None
            if pub_date:
                event_date = parse_date_from_text(pub_date.get_text(strip=True))
            if not event_date:
                event_date = parse_date_from_text(combined)
            if not event_date:
                continue
            if event_date < date.today():
                continue

            ticker = guess_ticker(combined)
            ticker_match = re.search(r'\(([A-Z]{2,5})\)', combined)
            if ticker_match:
                ticker = ticker_match.group(1)

            events.append({
                "ticker":     ticker,
                "company":    title_text[:100] or "FDA Advisory Committee",
                "event_type": "AdCom",
                "drug_name":  None,
                "indication": None,
                "event_date": event_date,
                "source":     "fda.gov/rss",
            })

        logger.info(f"FDA RSS scraper: {len(events)} upcoming AdCom events")

    except Exception as e:
        logger.warning(f"FDA RSS scrape failed ({e}) — relying on BiopharmaWatch only")

    return events
