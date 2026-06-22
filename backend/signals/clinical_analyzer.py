"""
Clinical trial deep fundamental analyzer.

Data sources (both free, no API key required):
  ClinicalTrials.gov API v2  — trial phase, enrollment, completion, results
  OpenFDA API                — company approval history, Complete Response Letters

Scoring:
  trial_phase_score     25%  — Phase 3 / PDUFA > Phase 2 > Phase 1
  enrollment_score      20%  — larger trial = more statistical power
  prior_results_score   30%  — prior Phase 2 success is the strongest predictor
  company_fda_score     25%  — company track record with FDA approvals
"""

import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

CT_API     = "https://clinicaltrials.gov/api/v2/studies"
OPENFDA    = "https://api.fda.gov/drug/drugsfda.json"
TIMEOUT    = 10


# ── ClinicalTrials.gov ─────────────────────────────────────────────────────────

def _search_clinicaltrials(drug_name: str, company: str) -> Optional[dict]:
    """Search ClinicalTrials.gov for the most relevant study."""
    terms = []
    if drug_name and len(drug_name) > 2:
        terms.append(drug_name)
    if company and len(company) > 2:
        # Use first two words of company name for better matching
        short_co = " ".join(company.split()[:2])
        terms.append(short_co)

    for term in terms:
        try:
            r = requests.get(CT_API, params={
                "query.term": term,
                "sort":       "@relevance",
                "pageSize":   5,
            }, timeout=TIMEOUT)

            if not r.ok:
                continue

            studies = r.json().get("studies", [])
            if not studies:
                continue

            # Prefer interventional studies with known phase; fall back to first result
            best = None
            for s in studies:
                proto  = s.get("protocolSection", {})
                design = proto.get("designModule", {})
                phases = design.get("phases", [])
                study_type = design.get("studyType", "")
                if "INTERVENTIONAL" in study_type.upper() and phases:
                    best = proto
                    break
            if best is None and studies:
                best = studies[0].get("protocolSection", {})
            if best:
                return _extract_study(best)

        except Exception as e:
            logger.debug(f"ClinicalTrials search failed for '{term}': {e}")

    return None


def _extract_study(proto: dict) -> dict:
    ident   = proto.get("identificationModule", {})
    status  = proto.get("statusModule", {})
    design  = proto.get("designModule", {})
    results = proto.get("resultsSection", {})

    phases = design.get("phases", [])
    phase_str = phases[0] if phases else ""

    enrollment = design.get("enrollmentInfo", {})
    enrollment_n = enrollment.get("count") or 0

    has_results  = bool(results)
    overall_status = status.get("overallStatus", "")
    why_stopped  = status.get("whyStopped", "")

    # Positive result signal: completed with results and not stopped early for safety
    stopped_bad = any(w in (why_stopped or "").lower()
                      for w in ["safety", "efficacy", "futility", "lack of efficacy"])

    return {
        "nct_id":        ident.get("nctId"),
        "title":         ident.get("briefTitle", ""),
        "phase":         phase_str,
        "enrollment":    enrollment_n,
        "status":        overall_status,
        "has_results":   has_results,
        "stopped_bad":   stopped_bad,
        "why_stopped":   why_stopped,
    }


def _score_trial_phase(phase_str: str) -> float:
    ph = (phase_str or "").upper()
    if "PHASE3" in ph.replace(" ", "") or "PHASE_3" in ph:
        return 90.0
    if "PHASE2" in ph.replace(" ", "") or "PHASE_2" in ph:
        return 65.0
    if "PHASE1" in ph.replace(" ", "") or "PHASE_1" in ph:
        return 35.0
    if "PHASE4" in ph.replace(" ", "") or "PHASE_4" in ph:
        return 95.0
    return 50.0


def _score_enrollment(n: int) -> float:
    """Larger enrollment = more statistical power."""
    if n >= 500:
        return 100.0
    if n >= 200:
        return 80.0
    if n >= 100:
        return 65.0
    if n >= 50:
        return 50.0
    if n > 0:
        return 35.0
    return 50.0  # unknown


def _score_prior_results(study: dict) -> float:
    """Score based on whether trial completed with results and how."""
    status = (study.get("status") or "").upper()
    has_results = study.get("has_results", False)
    stopped_bad = study.get("stopped_bad", False)

    if stopped_bad:
        return 10.0  # stopped for safety/futility — very bad

    if has_results and status == "COMPLETED":
        return 85.0  # completed with posted results — good signal

    if status == "COMPLETED" and not has_results:
        return 60.0  # completed but no posted results yet

    if status in ("ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION"):
        return 70.0  # ongoing, no issues

    if status == "RECRUITING":
        return 65.0  # still recruiting

    if status in ("TERMINATED", "WITHDRAWN"):
        return 5.0   # terminated — very bad

    if status == "SUSPENDED":
        return 20.0

    return 50.0


# ── OpenFDA ────────────────────────────────────────────────────────────────────

def _get_company_fda_history(company: str) -> dict:
    """
    Query OpenFDA for the company's approval history.
    Returns counts of approvals and Complete Response Letters.
    """
    if not company:
        return {"approvals": 0, "crls": 0}

    short = company.split()[0]  # first word of company name
    try:
        r = requests.get(OPENFDA, params={
            "search": f'openfda.manufacturer_name:"{short}"',
            "limit": 100,
            "count": "submissions.submission_type.exact",
        }, timeout=TIMEOUT)

        if not r.ok:
            return {"approvals": 0, "crls": 0}

        results = r.json().get("results", [])
        approvals = 0
        crls = 0
        for item in results:
            term = (item.get("term") or "").upper()
            count = item.get("count", 0)
            if term in ("ORIG", "SUPPL"):
                approvals += count
            elif "CRL" in term or "COMPLETE RESPONSE" in term:
                crls += count

        return {"approvals": approvals, "crls": crls}

    except Exception as e:
        logger.debug(f"OpenFDA query failed for '{company}': {e}")
        return {"approvals": 0, "crls": 0}


def _score_company_fda_track(fda_history: dict) -> float:
    """Score company FDA track record."""
    approvals = fda_history.get("approvals", 0)
    crls = fda_history.get("crls", 0)

    if approvals == 0 and crls == 0:
        return 50.0  # unknown / new company

    if crls > approvals:
        return 20.0  # more rejections than approvals — bad track record

    if approvals >= 5 and crls == 0:
        return 95.0  # strong proven track record

    if approvals >= 3 and crls <= 1:
        return 80.0

    if approvals >= 1 and crls == 0:
        return 70.0

    if approvals >= 1 and crls >= 1:
        return 50.0  # mixed

    return 40.0


# ── Main entry point ───────────────────────────────────────────────────────────

def analyze_clinical(
    ticker: str,
    drug_name: Optional[str] = None,
    company: Optional[str] = None,
    event_type: Optional[str] = None,
) -> dict:
    """
    Deep clinical analysis for an FDA catalyst stock.

    Returns:
        clinical_score   float 0-100
        clinical_detail  dict
    """
    study = _search_clinicaltrials(drug_name or "", company or "")
    fda_history = _get_company_fda_history(company or "")

    if study:
        s_phase   = _score_trial_phase(study.get("phase", ""))
        s_enroll  = _score_enrollment(study.get("enrollment", 0))
        s_results = _score_prior_results(study)
    else:
        # No trial found — use event_type as fallback
        from backend.signals.fundamental_analyzer import _score_event_type
        s_phase   = _score_event_type(event_type)
        s_enroll  = 50.0
        s_results = 50.0

    s_company = _score_company_fda_track(fda_history)

    clinical_score = (
        s_phase   * 0.25 +
        s_enroll  * 0.20 +
        s_results * 0.30 +
        s_company * 0.25
    )

    return {
        "clinical_score": round(clinical_score, 1),
        "clinical_detail": {
            "trial_found":    study is not None,
            "nct_id":         study.get("nct_id") if study else None,
            "trial_phase":    study.get("phase") if study else None,
            "enrollment":     study.get("enrollment") if study else None,
            "trial_status":   study.get("status") if study else None,
            "has_results":    study.get("has_results") if study else None,
            "stopped_bad":    study.get("stopped_bad") if study else None,
            "fda_approvals":  fda_history.get("approvals"),
            "fda_crls":       fda_history.get("crls"),
            "component_scores": {
                "trial_phase":   round(s_phase, 1),
                "enrollment":    round(s_enroll, 1),
                "prior_results": round(s_results, 1),
                "company_track": round(s_company, 1),
            },
        },
    }
