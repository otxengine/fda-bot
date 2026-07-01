"""
LLM-based learning engine for continuous signal improvement.

Two layers of learning:
  1. Negative event detector  — runs before each scan (Haiku, fast+cheap)
     Reads recent news + EDGAR for the ticker and flags red flags:
     trial stopped, FDA rejection, CRL, clinical hold, adverse events.
     Applies a score penalty (0-50 pts) that depresses composite_score.

  2. Outcome pattern analyzer — runs after history updates (Sonnet, deep)
     Reads last N HistoricalResult rows + their pre-event signals.
     Extracts which signal patterns predicted correct outcomes.
     Stores weight-adjustment recommendations that the next scan uses.

Results stored in LearningInsight table (models.py).
"""
import json
import logging
import os
import re
from datetime import datetime, date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Models
HAIKU  = "claude-haiku-4-5-20251001"   # fast/cheap — negative event scan per ticker
SONNET = "claude-sonnet-4-6"            # deep — weekly outcome analysis


def _get_client():
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except ImportError:
        logger.warning("anthropic package not installed — learning engine disabled")
        return None


def _parse_json_from_text(text: str) -> Optional[dict]:
    """Extract first JSON object from LLM response text."""
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


# ── 1. Negative Event Detector ─────────────────────────────────────────────────

def scan_ticker_for_negatives(
    ticker: str,
    company: str,
    event_type: str,
    db,
) -> dict:
    """
    Check recent news + EDGAR for red flags on this ticker.
    Returns {"negative": bool, "reason": str, "penalty": int (0-50)}

    Uses cached LearningInsight (24h TTL) to avoid re-querying.
    """
    from backend.models import LearningInsight

    # Check cache first (valid for 24h)
    cached = db.query(LearningInsight).filter(
        LearningInsight.ticker == ticker,
        LearningInsight.insight_type == "negative_event",
        LearningInsight.expires_at > datetime.utcnow(),
    ).order_by(LearningInsight.created_at.desc()).first()

    if cached:
        return json.loads(cached.insight_json)

    client = _get_client()
    if not client:
        return {"negative": False, "reason": "", "penalty": 0}

    # Gather news headlines from yfinance
    headlines = []
    try:
        import yfinance as yf
        news = yf.Ticker(ticker).news or []
        headlines = [n.get("title", "") for n in news[:8] if n.get("title")]
    except Exception as e:
        logger.debug(f"News fetch {ticker}: {e}")

    if not headlines:
        _cache_insight(db, ticker, "negative_event",
                       {"negative": False, "reason": "no news found", "penalty": 0},
                       ttl_hours=12)
        return {"negative": False, "reason": "", "penalty": 0}

    prompt = f"""You analyze FDA catalyst biotech stocks for negative signals before trading.

Ticker: {ticker} ({company})
Upcoming event: {event_type}

Recent news headlines:
{chr(10).join(f'- {h}' for h in headlines)}

Identify NEGATIVE FDA signals only: trial stopped for safety/futility, FDA rejection, Complete Response Letter (CRL), clinical hold, serious adverse events, going concern, or material setback.

Respond ONLY with JSON (no explanation):
{{"negative": true/false, "reason": "<one sentence or empty string>", "penalty": <0-50>}}

penalty guide: 0=no issue, 10=minor concern, 25=significant setback, 50=trial stopped/rejected"""

    try:
        response = client.messages.create(
            model=HAIKU,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_json_from_text(response.content[0].text)
        if result is None:
            result = {"negative": False, "reason": "", "penalty": 0}

        # Validate and clamp
        result["penalty"] = max(0, min(50, int(result.get("penalty", 0))))
        result["negative"] = bool(result.get("negative", False))
        result["reason"]   = str(result.get("reason", ""))[:200]

        ttl = 24 if not result["negative"] else 48
        _cache_insight(db, ticker, "negative_event", result, ttl_hours=ttl)

        if result["negative"]:
            logger.info(f"Negative event detected: {ticker} — {result['reason']} (penalty -{result['penalty']})")

        return result

    except Exception as e:
        logger.debug(f"Negative event scan {ticker}: {e}")
        return {"negative": False, "reason": "", "penalty": 0}


# ── 2. Outcome Pattern Analyzer ────────────────────────────────────────────────

def analyze_outcome_patterns(db) -> Optional[dict]:
    """
    Analyze recent HistoricalResult records with Claude Sonnet.
    Extracts patterns → stores weight-adjustment recommendations.
    Returns insights dict or None if insufficient data.
    """
    from backend.models import HistoricalResult, LearningInsight

    records = (
        db.query(HistoricalResult)
        .filter(
            HistoricalResult.change_1d_pct.isnot(None),
            HistoricalResult.pre_event_score.isnot(None),
        )
        .order_by(HistoricalResult.event_date.desc())
        .limit(60)
        .all()
    )

    if len(records) < 5:
        logger.info("Learning: not enough outcome data yet (need 5+)")
        return None

    client = _get_client()
    if not client:
        return None

    # Build dataset summary
    rows = []
    for r in records:
        outcome_str = r.outcome or ("up" if (r.change_1d_pct or 0) > 0 else "down")
        rows.append(
            f"ticker={r.ticker} event={r.event_type or '?'} "
            f"score={r.pre_event_score:.0f} cp={r.pre_event_call_put_ratio or 0:.1f} "
            f"iv_rank={r.pre_event_iv_rank or 0:.0f} "
            f"change_1d={r.change_1d_pct:+.1f}% outcome={outcome_str}"
        )

    correct = sum(
        1 for r in records
        if (r.pre_event_call_put_ratio or 1) >= 1.8
        and (r.change_1d_pct or 0) > 3
    )
    total_bullish = sum(1 for r in records if (r.pre_event_call_put_ratio or 1) >= 1.8)
    accuracy = round(correct / total_bullish * 100) if total_bullish > 0 else 50

    prompt = f"""You are a quantitative analyst improving a biotech FDA catalyst trading signal system.

The system scores stocks 0-100 using these components:
- expiration_score (30%): how concentrated options volume is near the FDA event date
- iv_rank (17%): implied volatility rank 0-100
- call_put (17%): call/put volume ratio (bullish flow)
- vol_oi (13%): volume/open-interest ratio
- premium (8%): dollar premium flow
- fundamental (15%): cash runway, analyst consensus, clinical trial quality

Recent {len(records)} outcomes (pre-event signal → 1-day price change after FDA event):
{chr(10).join(rows[:40])}

Current bullish signal accuracy (C/P≥1.8 predicted +3%): {accuracy}% of {total_bullish} bullish signals

Analyze what patterns predicted correct outcomes and respond ONLY with JSON:
{{
  "accuracy_rate": {accuracy},
  "sample_size": {len(records)},
  "best_predictor": "<which single feature best predicted outcomes>",
  "worst_predictor": "<which feature was least reliable>",
  "weight_adjustments": {{
    "expiration": <-15 to +15>,
    "iv_rank": <-15 to +15>,
    "call_put": <-15 to +15>,
    "vol_oi": <-15 to +15>,
    "premium": <-15 to +15>,
    "fundamental": <-15 to +15>
  }},
  "score_threshold_recommendation": <40-65, suggested minimum score for BUY>,
  "cp_threshold_recommendation": <1.2-3.0, suggested minimum C/P for BUY>,
  "avoid_patterns": ["<pattern>"],
  "strong_patterns": ["<pattern>"],
  "confidence": <0.0-1.0>,
  "summary": "<2-sentence summary for the trader>"
}}"""

    try:
        response = client.messages.create(
            model=SONNET,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        insights = _parse_json_from_text(response.content[0].text)
        if not insights:
            return None

        # Clamp weight adjustments to ±15
        wadj = insights.get("weight_adjustments", {})
        for k in wadj:
            wadj[k] = max(-15, min(15, wadj[k]))
        insights["weight_adjustments"] = wadj

        # Store as a long-lived insight (30 days)
        _cache_insight(db, None, "weight_adjustment", insights, ttl_hours=24 * 30)

        logger.info(
            f"Learning: outcome analysis complete. "
            f"accuracy={insights.get('accuracy_rate')}% "
            f"confidence={insights.get('confidence')}"
        )
        return insights

    except Exception as e:
        logger.error(f"Outcome pattern analysis failed: {e}")
        return None


# ── 3. Load active learned weights ─────────────────────────────────────────────

def get_learned_weight_adjustments(db) -> dict:
    """
    Return the most recent weight-adjustment insight (if confidence ≥ 0.5).
    Used by analyzer.py to tune scoring weights.
    """
    from backend.models import LearningInsight

    insight = (
        db.query(LearningInsight)
        .filter(
            LearningInsight.insight_type == "weight_adjustment",
            LearningInsight.expires_at > datetime.utcnow(),
        )
        .order_by(LearningInsight.created_at.desc())
        .first()
    )

    if not insight:
        return {}

    try:
        data = json.loads(insight.insight_json)
        if data.get("confidence", 0) >= 0.5:
            return data.get("weight_adjustments", {})
    except Exception:
        pass
    return {}


def get_score_thresholds(db) -> dict:
    """Return learned score/CP thresholds from latest weight_adjustment insight."""
    from backend.models import LearningInsight

    insight = (
        db.query(LearningInsight)
        .filter(
            LearningInsight.insight_type == "weight_adjustment",
            LearningInsight.expires_at > datetime.utcnow(),
        )
        .order_by(LearningInsight.created_at.desc())
        .first()
    )

    if not insight:
        return {}

    try:
        data = json.loads(insight.insight_json)
        return {
            "score_threshold": data.get("score_threshold_recommendation"),
            "cp_threshold":    data.get("cp_threshold_recommendation"),
        }
    except Exception:
        return {}


# ── 4. Weekly learning Telegram digest ─────────────────────────────────────────

def build_learning_digest(insights: dict) -> str:
    """Build Hebrew Telegram message from learning insights."""
    acc   = insights.get("accuracy_rate", "?")
    n     = insights.get("sample_size", 0)
    conf  = insights.get("confidence", 0)
    best  = insights.get("best_predictor", "—")
    worst = insights.get("worst_predictor", "—")
    summ  = insights.get("summary", "")
    avoid = insights.get("avoid_patterns", [])
    strong = insights.get("strong_patterns", [])

    score_thr = insights.get("score_threshold_recommendation")
    cp_thr    = insights.get("cp_threshold_recommendation")

    wadj = insights.get("weight_adjustments", {})
    adj_lines = []
    for k, v in wadj.items():
        if abs(v) >= 3:
            arrow = "↑" if v > 0 else "↓"
            adj_lines.append(f"  {arrow} {k}: {v:+d}")

    msg = (
        f"🧠 <b>דוח למידה שבועי — בוט FDA</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>דיוק אחרון:</b>  {acc}% מתוך {n} עסקאות\n"
        f"<b>ביטחון:</b>      {int(conf*100)}%\n\n"
        f"<b>מנבא הכי חזק:</b>  {best}\n"
        f"<b>מנבא הכי חלש:</b> {worst}\n"
    )

    if adj_lines:
        msg += f"\n<b>שינויי משקל שהוחלו:</b>\n" + "\n".join(adj_lines) + "\n"

    if score_thr:
        msg += f"\n<b>סף ציון מומלץ:</b> {score_thr:.0f} | <b>סף C/P:</b> {cp_thr:.1f}\n"

    if strong:
        msg += f"\n<b>דפוסים חזקים:</b>\n" + "\n".join(f"  ✅ {p}" for p in strong[:3])

    if avoid:
        msg += f"\n<b>דפוסים להימנע:</b>\n" + "\n".join(f"  ⚠️ {p}" for p in avoid[:3])

    if summ:
        msg += f"\n\n<i>{summ}</i>"

    return msg


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _cache_insight(db, ticker, insight_type, data: dict, ttl_hours: int = 24):
    """Save a LearningInsight record."""
    from backend.models import LearningInsight
    try:
        insight = LearningInsight(
            ticker=ticker,
            insight_type=insight_type,
            insight_json=json.dumps(data),
            confidence=float(data.get("confidence", 0.5)),
            sample_size=int(data.get("sample_size", 0)),
            expires_at=datetime.utcnow() + timedelta(hours=ttl_hours),
        )
        db.add(insight)
        db.flush()
    except Exception as e:
        logger.debug(f"Cache insight failed: {e}")
