"""
EDGAR EFTS 8-K scanner for micro-cap biotech FDA catalysts.

Many small biotechs (<$50M market cap) have NO options but can still move 30-100%
on FDA decisions. This scanner queries SEC EDGAR's full-text search for recent
8-K filings containing FDA keywords, extracts tickers, and returns event-like dicts
so the scheduler can fire stock-only (not options) alerts.

Results have source="edgar/8-K" and event_type="FDA Filing (8-K)".
"""
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

EDGAR_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

# Keywords to search — broadened beyond just PDUFA to catch CRL, approval, etc.
EDGAR_QUERIES = [
    '"PDUFA" "FDA"',
    '"Complete Response Letter"',
    '"FDA approval" "NDA"',
    '"FDA approval" "BLA"',
    '"Advisory Committee" "FDA"',
    '"Fast Track" "FDA approval"',
    '"Breakthrough Therapy" "FDA"',
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FDA-scanner research bot; contact@example.com)",
    "Accept": "application/json",
}

# Positive signals (stock likely to move UP)
POSITIVE_KEYWORDS = [
    "approved", "approval", "pdufa", "priority review", "breakthrough therapy",
    "fast track", "accelerated approval", "nda accepted", "bla accepted",
    "complete response letter resubmission", "advisory committee vote",
]

# Negative signals (stock likely to move DOWN or already crashed)
NEGATIVE_KEYWORDS = [
    "complete response letter", "crl", "clinical hold", "trial stopped",
    "safety concern", "adverse event", "failed", "did not meet", "missed endpoint",
    "withdrew", "refusal to file",
]


def _extract_ticker_from_filing(filing: dict) -> Optional[str]:
    """Try to get the ticker from the filing's entity information."""
    # EDGAR sometimes includes ticker in display_names or entity_id
    entity = filing.get("entity_name", "") or filing.get("display_names", [""])[0]
    # entity_id is the CIK; we can't map CIK → ticker without another lookup
    # but we'll store the CIK and company name for downstream use
    return None  # ticker extracted separately via CIK lookup


def _cik_to_ticker(cik: str) -> Optional[str]:
    """Lookup ticker from CIK via EDGAR company search."""
    try:
        url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        tickers = data.get("tickers", [])
        if tickers:
            return tickers[0].upper()
    except Exception:
        pass
    return None


def _classify_sentiment(text: str) -> str:
    """Returns 'positive', 'negative', or 'neutral' based on filing text."""
    text_lower = text.lower()
    neg_hits = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_lower)
    pos_hits = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_lower)
    if neg_hits > pos_hits:
        return "negative"
    if pos_hits > 0:
        return "positive"
    return "neutral"


def scan_edgar_fda_filings(
    lookback_days: int = 3,
    max_results: int = 50,
) -> list[dict]:
    """
    Query EDGAR EFTS for recent 8-K filings mentioning FDA keywords.
    Returns list of event-like dicts for downstream alert pipeline.

    Each result includes:
        ticker, company, event_type, event_date, source,
        _sentiment, _cik, _filing_url
    """
    today = date.today()
    date_from = (today - timedelta(days=lookback_days)).isoformat()
    date_to = today.isoformat()

    results = []
    seen_ciks: set[str] = set()

    for query in EDGAR_QUERIES:
        try:
            params = {
                "q": query,
                "dateRange": "custom",
                "startdt": date_from,
                "enddt": date_to,
                "forms": "8-K",
                "_source": "hits.hits._source",
                "hits.hits.total.value": max_results,
            }
            r = requests.get(
                "https://efts.sec.gov/LATEST/search-index",
                params=params,
                headers=HEADERS,
                timeout=15,
            )
            if r.status_code != 200:
                logger.debug(f"EDGAR EFTS returned {r.status_code} for query: {query}")
                continue

            data = r.json()
            hits = data.get("hits", {}).get("hits", [])

            for hit in hits:
                src = hit.get("_source", {})
                cik = src.get("entity_id") or src.get("file_num", "")
                if not cik or cik in seen_ciks:
                    continue
                seen_ciks.add(cik)

                company = (src.get("display_names") or [None])[0] or src.get("entity_name", "")
                if isinstance(company, list):
                    company = company[0] if company else ""
                # Clean up "(CIK 0001234567)" suffix
                company = re.sub(r"\s*\(CIK\s+\d+\)", "", company).strip()

                filed_at_str = src.get("file_date") or src.get("period_of_report") or date_to
                try:
                    event_date = date.fromisoformat(filed_at_str[:10])
                except Exception:
                    event_date = today

                # Get filing URL for news/sentiment extraction
                accession = src.get("accession_no", "").replace("-", "")
                filing_url = (
                    f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{src.get('file_num','')}"
                    if accession else None
                )

                # Extract sentiment from description/filing text snippet
                snippet = src.get("description") or src.get("form_type", "")
                sentiment = _classify_sentiment(snippet)

                # Try to get ticker (CIK lookup — cached via seen_ciks dedup)
                ticker = _cik_to_ticker(cik)

                results.append({
                    "ticker":      ticker,      # may be None — downstream will skip or use company
                    "company":     company,
                    "event_type":  "FDA Filing (8-K)",
                    "drug_name":   None,
                    "indication":  None,
                    "event_date":  event_date,
                    "source":      "edgar/8-K",
                    "_sentiment":  sentiment,
                    "_cik":        cik,
                    "_filing_url": filing_url,
                    "_query":      query,
                })

                if len(results) >= max_results:
                    break

        except Exception as e:
            logger.warning(f"EDGAR scan error for query '{query}': {e}")
            continue

    logger.info(f"EDGAR 8-K scan: found {len(results)} filings in last {lookback_days}d")
    return results


def scan_edgar_for_ticker(ticker: str, lookback_days: int = 30) -> list[dict]:
    """
    Search EDGAR for recent 8-K filings specifically for one ticker/company.
    Used by the negative event detector to check if a ticker has recent bad news.
    """
    today = date.today()
    date_from = (today - timedelta(days=lookback_days)).isoformat()

    try:
        params = {
            "q": f'"{ticker}" "FDA"',
            "dateRange": "custom",
            "startdt": date_from,
            "enddt": today.isoformat(),
            "forms": "8-K",
        }
        r = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params=params,
            headers=HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        hits = data.get("hits", {}).get("hits", [])
        results = []
        for hit in hits[:5]:
            src = hit.get("_source", {})
            snippet = src.get("description") or ""
            sentiment = _classify_sentiment(snippet)
            filed = src.get("file_date") or today.isoformat()
            results.append({
                "ticker":     ticker,
                "filed_date": filed[:10],
                "sentiment":  sentiment,
                "snippet":    snippet[:300],
            })
        return results
    except Exception as e:
        logger.debug(f"EDGAR ticker search {ticker}: {e}")
        return []
