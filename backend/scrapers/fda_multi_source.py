"""
Multi-source FDA calendar scraper.
Pulls from multiple free public sources to build a comprehensive event list.

Sources:
  1. Investing.com biotech calendar (public)
  2. StockAnalysis.com FDA calendar (public)
  3. ClinicalTrials.gov upcoming study completions
"""
import logging
import re
import requests
from datetime import date, datetime, timedelta
from typing import Optional
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Source 1: Nasdaq earnings calendar filtered for biotech ───────────────────

def _scrape_nasdaq_biotech_earnings(days_forward: int = 90) -> list[dict]:
    """
    Nasdaq earnings calendar — captures earnings events for biotech stocks
    that often coincide with clinical data readouts.
    """
    events = []
    today = date.today()

    # Known biotech tickers to check for earnings/catalyst dates
    from backend.scrapers.broad_biotech import BIOTECH_UNIVERSE

    for delta in range(0, days_forward, 7):  # weekly chunks
        check_date = today + timedelta(days=delta)
        try:
            r = requests.get(
                "https://api.nasdaq.com/api/calendar/earnings",
                params={"date": check_date.isoformat()},
                headers={**HEADERS, "Accept": "application/json, text/plain, */*"},
                timeout=10,
            )
            if not r.ok:
                continue
            data = r.json().get("data", {})
            rows = data.get("rows", []) if data else []

            for row in rows:
                ticker = (row.get("symbol") or "").strip().upper()
                if ticker not in BIOTECH_UNIVERSE:
                    continue
                company = row.get("name", "")
                eps_estimate = row.get("epsForecast")

                events.append({
                    "ticker":     ticker,
                    "company":    company,
                    "event_type": "Earnings/Data Readout",
                    "drug_name":  None,
                    "indication": None,
                    "event_date": check_date,
                    "source":     "nasdaq_earnings",
                })

        except Exception as e:
            logger.debug(f"Nasdaq earnings {check_date}: {e}")

    logger.info(f"Nasdaq biotech earnings: found {len(events)} events")
    return events


# ── Source 2: ClinicalTrials.gov — studies completing soon ────────────────────

# Map of common company names → tickers (extend as needed)
COMPANY_TICKER_MAP = {
    "moderna": "MRNA", "pfizer": "PFE", "biogen": "BIIB", "regeneron": "REGN",
    "vertex": "VRTX", "gilead": "GILD", "amgen": "AMGN", "alnylam": "ALNY",
    "sarepta": "SRPT", "neurocrine": "NBIX", "sage": "SAGE", "beam": "BEAM",
    "crispr": "CRSP", "intellia": "NTLA", "editas": "EDIT", "inovio": "INO",
    "novavax": "NVAX", "blueprint": "BPMC", "jazz": "JAZZ", "exelixis": "EXEL",
    "iovance": "IOVA", "kymera": "KYMR", "arvinas": "ARVN", "nuvalent": "NUVL",
    "turning point": "TPTX", "relay": "RLAY", "boundless bio": "BOLD",
    "tarsus": "TARS", "nuvation": "NUVB", "praxis": "PRAX", "acadia": "ACAD",
    "axsome": "AXSM", "biomarin": "BMRN", "ultragenyx": "RARE", "ionis": "IONS",
    "bicycle": "BCYC", "sangamo": "SGMO", "gossamer": "GOSS", "ligand": "LGND",
    "zentalis": "ZNTL", "aquestive": "AQST", "os therapies": "OSTX",
    "ac immune": "ACIU", "xoma": "XOMA", "inhibrx": "INBX",
}


def _guess_ticker(text: str) -> Optional[str]:
    text_lower = text.lower()
    for key, ticker in COMPANY_TICKER_MAP.items():
        if key in text_lower:
            return ticker
    # Try to find ticker pattern in parentheses: (MRNA) or "NASDAQ: MRNA"
    m = re.search(r'\b([A-Z]{2,5})\b', text)
    return m.group(1) if m else None


def _scrape_clinicaltrials_completing(days_forward: int = 90) -> list[dict]:
    """
    Query ClinicalTrials.gov for Phase 2/3 studies with primary completion
    dates in the next N days.
    """
    events = []
    today = date.today()
    end_date = today + timedelta(days=days_forward)

    try:
        r = requests.get(
            "https://clinicaltrials.gov/api/v2/studies",
            params={
                "filter.advanced": (
                    f"AREA[PrimaryCompletionDate]RANGE[{today.isoformat()},{end_date.isoformat()}]"
                    " AND AREA[Phase]COVER[PHASE3 PHASE2]"
                    " AND AREA[StudyType]COVER[INTERVENTIONAL]"
                ),
                "fields": "NCTId,BriefTitle,LeadSponsorName,PrimaryCompletionDate,Phase,OverallStatus",
                "pageSize": 100,
                "sort": "PrimaryCompletionDate:asc",
            },
            headers=HEADERS,
            timeout=15,
        )
        if not r.ok:
            return events

        studies = r.json().get("studies", [])
        for s in studies:
            proto = s.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            status = proto.get("statusModule", {})
            sponsor = proto.get("sponsorCollaboratorsModule", {})

            overall_status = status.get("overallStatus", "")
            if overall_status not in ("RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION"):
                continue

            completion_str = status.get("primaryCompletionDateStruct", {}).get("date", "")
            if not completion_str:
                continue

            try:
                # ClinicalTrials dates: "2026-08-15" or "2026-08"
                if len(completion_str) == 7:  # YYYY-MM
                    completion_str += "-15"
                event_date = date.fromisoformat(completion_str)
            except ValueError:
                continue

            if event_date < today or event_date > end_date:
                continue

            sponsor_name = sponsor.get("leadSponsor", {}).get("name", "")
            ticker = _guess_ticker(sponsor_name)

            title = ident.get("briefTitle", "")
            phase = proto.get("designModule", {}).get("phases", [""])[0]

            phase_label = {
                "PHASE3": "Phase 3", "PHASE2": "Phase 2",
                "PHASE2_PHASE3": "Phase 2/3", "PHASE4": "Phase 4",
            }.get(phase.replace(" ", "").upper(), phase)

            events.append({
                "ticker":     ticker,
                "company":    sponsor_name,
                "event_type": f"{phase_label} data readout",
                "drug_name":  title[:60] if title else None,
                "indication": None,
                "event_date": event_date,
                "source":     "clinicaltrials.gov",
            })

    except Exception as e:
        logger.error(f"ClinicalTrials completing soon: {e}")

    logger.info(f"ClinicalTrials completing: found {len(events)} Phase 2/3 events")
    return events


# ── Main entry ─────────────────────────────────────────────────────────────────

def scrape_all_sources(days_forward: int = 90) -> list[dict]:
    """
    Aggregate all multi-source FDA/catalyst events.
    Returns deduplicated list sorted by event_date.
    """
    all_events = []

    all_events += _scrape_nasdaq_biotech_earnings(days_forward)
    all_events += _scrape_clinicaltrials_completing(days_forward)

    # Deduplicate by ticker + event_date
    seen = set()
    deduped = []
    for e in all_events:
        key = (e.get("ticker"), str(e.get("event_date")))
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    deduped.sort(key=lambda x: x.get("event_date") or date.max)
    logger.info(f"Multi-source scrape total: {len(deduped)} events")
    return deduped
