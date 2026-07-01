"""
APScheduler configuration for periodic FDA calendar and options data updates.
"""
import logging
from datetime import datetime, time
import pytz

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

EST = pytz.timezone("America/New_York")


def is_market_hours() -> bool:
    """Check if current time is within market hours (9:30-16:00 EST, Mon-Fri)."""
    now_est = datetime.now(EST)
    if now_est.weekday() >= 5:  # weekend
        return False
    market_open = time(9, 30)
    market_close = time(16, 0)
    current_time = now_est.time()
    return market_open <= current_time <= market_close


def run_fda_scrape():
    """Job: scrape FDA calendar and update events table."""
    try:
        from backend.database import SessionLocal
        from backend.scrapers.fda_calendar import scrape_fda_calendar
        from backend.scrapers.biopharma import scrape_biopharmawatch
        from backend.models import FdaEvent

        logger.info("Starting FDA calendar scrape...")
        db = SessionLocal()

        from backend.scrapers.biopharmcatalyst import scrape_biopharmcatalyst
        from backend.scrapers.broad_biotech import scan_broad_biotech
        from backend.scrapers.fda_multi_source import scrape_all_sources
        from backend.scrapers.edgar_pdufa import scrape_edgar_pdufa
        fda_events   = scrape_fda_calendar()
        bpw_events   = scrape_biopharmawatch()
        bpc_events   = scrape_biopharmcatalyst()
        edgar_events = scrape_edgar_pdufa()
        multi_events = scrape_all_sources(days_forward=90)
        broad_events = scan_broad_biotech(iv_rank_threshold=60, max_tickers=150)
        all_events   = fda_events + bpw_events + bpc_events + edgar_events + multi_events + broad_events

        added = 0
        for event_data in all_events:
            # Deduplicate: prefer ticker+date (works across sources with different company names)
            ticker = event_data.get("ticker")
            if ticker:
                existing = db.query(FdaEvent).filter(
                    FdaEvent.ticker == ticker,
                    FdaEvent.event_date == event_data["event_date"],
                ).first()
            else:
                existing = db.query(FdaEvent).filter(
                    FdaEvent.event_date == event_data["event_date"],
                    FdaEvent.company == event_data["company"],
                ).first()

            if not existing:
                event = FdaEvent(
                    ticker=event_data.get("ticker"),
                    company=event_data["company"],
                    event_type=event_data.get("event_type", "Unknown"),
                    drug_name=event_data.get("drug_name"),
                    indication=event_data.get("indication"),
                    event_date=event_data["event_date"],
                    source=event_data.get("source", "unknown"),
                )
                db.add(event)
                added += 1

        db.commit()
        db.close()
        logger.info(f"FDA scrape complete: {added} new events added")

    except Exception as e:
        logger.error(f"FDA scrape job failed: {e}")


def run_realtime_scan(days_window: int = 7):
    """
    Unified high-frequency scan using both options-flow AND volume-spike detection.
    - Stocks with options (price > $3): options-flow analysis
    - Penny/micro-caps (price < $3, no options): volume-spike analysis
    Only BUY signals are alerted. AlertLog cooldown: 4h per ticker.
    """
    try:
        from datetime import date, timedelta, datetime
        from backend.database import SessionLocal
        from backend.models import OptionsSignal, AlertLog
        from backend.data.polygon import PolygonClient
        from backend.data.yfinance_client import YFinanceClient
        from backend.signals.unified_scanner import scan_all_events

        db = SessionLocal()
        today = date.today()
        polygon = PolygonClient()
        yf_client = YFinanceClient()
        cooldown_cutoff = datetime.utcnow() - timedelta(hours=4)

        # Unified scan — handles both options and penny paths automatically
        signals = scan_all_events(
            db=db,
            days_window=days_window,
            polygon_client=polygon,
            yfinance_client=yf_client,
        )

        buy_signals = []

        for result in signals:
            ticker = result.get("ticker")
            if not ticker:
                continue

            # Cooldown check
            recent = db.query(AlertLog).filter(
                AlertLog.ticker == ticker,
                AlertLog.alert_type == "stock_buy",
                AlertLog.triggered_at >= cooldown_cutoff,
            ).first()
            if recent:
                continue

            # Persist signal to DB
            db_fields = {k: v for k, v in result.items() if not k.startswith("_")}
            try:
                signal = OptionsSignal(**db_fields)
                db.add(signal)
            except Exception as ex:
                logger.debug(f"DB write skip {ticker}: {ex}")

            days_until = result.get("_days_until") or (
                (result.get("target_date") and
                 (__import__("datetime").date.fromisoformat(result["target_date"]) + timedelta(days=1)
                  - today).days) or 0
            )

            db.add(AlertLog(
                ticker=ticker,
                alert_type="stock_buy",
                score_at_trigger=result.get("composite_score"),
                message=(
                    f"BUY {ticker} score={result.get('composite_score',0):.0f} "
                    f"path={result.get('_scan_path','?')} "
                    f"[{days_window}d scan]"
                ),
            ))

            scan_path = result.get("_scan_path", "options")
            buy_signals.append({
                "ticker":            ticker,
                "company":           result.get("company") or result.get("ticker"),
                "event_type":        result.get("event_type") or "FDA Event",
                "days_until":        days_until,
                "entry_price":       result.get("entry_price"),
                "stop_loss":         result.get("stop_loss_price"),
                "target_date":       result.get("target_date"),
                "score":             result.get("composite_score", 0),
                "reason":            result.get("stock_signal_reason"),
                "expected_move":     result.get("expected_move_pct"),
                "call_put_ratio":    result.get("call_put_ratio"),
                "fundamental_score": result.get("fundamental_score"),
                "clinical_score":    result.get("clinical_score"),
                "analyst_bullish":   result.get("analyst_bullish"),
                "squeeze_setup":     result.get("squeeze_setup"),
                "binary_event_risk": result.get("binary_event_risk", 0),
                # Penny-specific extras
                "_scan_path":        scan_path,
                "_volume_spike":     result.get("_volume_spike"),
                "_momentum_3d":      result.get("_momentum_3d"),
                "_short_pct":        result.get("_short_pct"),
                "_risk_level":       result.get("_risk_level"),
                "_position_advice":  result.get("_position_advice"),
            })

        db.commit()
        db.close()

        if buy_signals:
            _notify_stock_buy_signals(buy_signals)
            logger.info(f"Unified scan ({days_window}d): {len(buy_signals)} BUY alerts sent")
        else:
            logger.debug(f"Unified scan ({days_window}d): no new BUY signals")

    except Exception as e:
        logger.error(f"Realtime scan ({days_window}d) failed: {e}")


def run_urgent_scan():
    """Every 5 min during market hours — events 0-2 days away (most time-sensitive)."""
    run_realtime_scan(days_window=2)


def run_focused_scan():
    """Every 15 min during extended hours — events 0-7 days away."""
    run_realtime_scan(days_window=7)


def run_options_scan(force: bool = False):
    """Job: full scan of all 60-day events — builds signal history, runs hourly."""
    if not force and not is_market_hours():
        logger.debug("Outside market hours - skipping full options scan")
        return

    try:
        from datetime import date, timedelta
        from backend.database import SessionLocal
        from backend.models import FdaEvent, OptionsSignal
        from backend.data.polygon import PolygonClient
        from backend.data.yfinance_client import YFinanceClient
        from backend.signals.analyzer import analyze_ticker

        logger.info("Starting options scan...")
        db = SessionLocal()
        polygon = PolygonClient()
        yf_client = YFinanceClient()

        # Scan all events up to 60 days (build signal data), alert only on 0-7 day window
        today = date.today()
        cutoff = today + timedelta(days=60)
        events = db.query(FdaEvent).filter(
            FdaEvent.event_date >= today,
            FdaEvent.event_date <= cutoff,
            FdaEvent.ticker.isnot(None),
        ).all()

        scanned = 0
        new_buy_signals = []

        for event in events:
            if not event.ticker:
                continue

            prev_signal = (
                db.query(OptionsSignal)
                .filter(OptionsSignal.ticker == event.ticker)
                .order_by(OptionsSignal.scan_time.desc())
                .first()
            )

            result = analyze_ticker(
                ticker=event.ticker,
                polygon_client=polygon,
                yfinance_client=yf_client,
                event_date=event.event_date,
                event_type=event.event_type,
                drug_name=event.drug_name,
                company=event.company,
                db=db,
                fda_event_id=event.id,
            )
            if result:
                signal = OptionsSignal(**{k: v for k, v in result.items() if not k.startswith("_")})
                db.add(signal)
                scanned += 1

                # Send BUY alert only when:
                # 1. signal flipped to BUY (score≥50 + bullish flow)
                # 2. AND event is within 0-7 days (prime catalyst window)
                days_until = (event.event_date - today).days
                prev_stock_signal = prev_signal.stock_signal if prev_signal else None
                new_stock_signal = result.get("stock_signal")
                if (new_stock_signal == "BUY"
                        and prev_stock_signal != "BUY"
                        and 0 <= days_until <= 7):
                    new_buy_signals.append({
                        "ticker":           event.ticker,
                        "company":          event.company,
                        "event_type":       event.event_type,
                        "days_until":       days_until,
                        "entry_price":      result.get("entry_price"),
                        "stop_loss":        result.get("stop_loss_price"),
                        "target_date":      result.get("target_date"),
                        "score":            result.get("composite_score"),
                        "reason":           result.get("stock_signal_reason"),
                        "expected_move":    result.get("expected_move_pct"),
                        "call_put_ratio":   result.get("call_put_ratio"),
                        "fundamental_score": result.get("fundamental_score"),
                        "clinical_score":   result.get("clinical_score"),
                        "analyst_bullish":  result.get("analyst_bullish"),
                        "squeeze_setup":    result.get("squeeze_setup"),
                    })

        db.commit()
        db.close()
        logger.info(f"Options scan complete: {scanned} tickers analyzed")

        # Send BUY alerts only — no WATCH spam
        if new_buy_signals:
            _notify_stock_buy_signals(new_buy_signals)

    except Exception as e:
        logger.error(f"Options scan job failed: {e}")


def _notify_stock_buy_signals(signals: list):
    """Send Telegram BUY signal alert. Handles both options-flow and penny/volume-spike paths."""
    try:
        from backend.signals.alerter import send_alert

        for sig in signals:
            ticker  = sig["ticker"]
            entry   = sig.get("entry_price")
            stop    = sig.get("stop_loss")
            score   = sig.get("score") or 0
            target_date = sig.get("target_date", "")
            scan_path = sig.get("_scan_path", "options")

            entry_str = f"${entry:.4f}" if entry and entry < 1 else (f"${entry:.2f}" if entry else "market")
            stop_str  = f"${stop:.4f}" if stop and stop < 1 else (f"${stop:.2f}" if stop else "N/A")
            risk_pct  = f"{((stop-entry)/entry*100):.1f}%" if entry and stop else "—"

            if scan_path == "penny":
                # ── Penny / volume-spike path ─────────────────────────────────
                spike   = sig.get("_volume_spike") or 0
                mom     = sig.get("_momentum_3d") or 0
                short   = sig.get("_short_pct") or 0
                risk    = sig.get("_risk_level", "HIGH")
                advice  = sig.get("_position_advice", "גודל פוזיציה קטן בלבד (0.5%)")
                risk_emoji = "🔥" if risk == "HIGH" else "⚠️"
                mom_str = f"+{mom:.1f}%" if mom >= 0 else f"{mom:.1f}%"
                short_line = f"<b>שורט:</b>        {short:.0f}% 🔥\n" if short >= 20 else ""

                tg_text = (
                    f"🟢 <b>קנייה — {ticker}</b>\n"
                    f"<i>{sig.get('company','')}</i>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"<b>כניסה:</b>       {entry_str}\n"
                    f"<b>סטופ לוס:</b>    {stop_str} ({risk_pct})\n"
                    f"<b>יציאה לפני:</b>  {target_date}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"<b>נפח יומי:</b>    ×{spike:.1f} מעל ממוצע\n"
                    f"<b>מומנטום 3י׳:</b>  {mom_str}\n"
                    f"{short_line}"
                    f"<b>ציון:</b>        {score:.0f}/100\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"<b>אירוע:</b> {sig.get('event_type','FDA')} בעוד {sig.get('days_until','?')} ימים\n"
                    f"<b>סיבה:</b> {sig.get('reason','')}\n\n"
                    f"{risk_emoji} <b>סיכון {risk}</b> — {advice}\n"
                    f"⚠️ <i>אין אופציות — כניסה דרך המניה בלבד</i>\n"
                    f"🚪 <b>צא יום לפני ה-FDA</b>"
                )
                plain = f"BUY {ticker} @ {entry_str} vol×{spike:.1f} mom{mom_str} | FDA in {sig.get('days_until','?')}d | score {score:.0f}"

            else:
                # ── Options-flow path ─────────────────────────────────────────
                em   = sig.get("expected_move")
                cp   = sig.get("call_put_ratio") or 1.0
                fund = sig.get("fundamental_score")
                clinical = sig.get("clinical_score")

                target_val = entry * (1 + (em or 10) / 100) if entry and em else None
                target_str = f"${target_val:.2f}" if target_val else "N/A"

                direction = (
                    (f"↑ +{em:.1f}%" if em else "↑ עלייה") if cp >= 2.0 else
                    (f"↕ ±{em:.1f}%" if em else "↕ ניטרלי")
                )

                flags = []
                if sig.get("analyst_bullish"): flags.append("📊 אנליסטים חיוביים")
                if sig.get("squeeze_setup"):   flags.append("🔥 שורט גבוה — squeeze")
                if sig.get("binary_event_risk"): flags.append("⚠️ binary risk")
                flags_str = "\n" + " | ".join(flags) if flags else ""

                fund_str = f"{fund:.0f}" if fund is not None else "—"
                clin_str = f"{clinical:.0f}" if clinical is not None else "—"

                tg_text = (
                    f"🟢 <b>קנייה — {ticker}</b>\n"
                    f"<i>{sig.get('company','')}</i>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"<b>כניסה:</b>       {entry_str}\n"
                    f"<b>סטופ לוס:</b>    {stop_str} ({risk_pct})\n"
                    f"<b>יעד מחיר:</b>    {target_str}\n"
                    f"<b>יציאה לפני:</b>  {target_date}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"<b>תחזית מניה:</b>  {direction}\n"
                    f"<b>זרימת קול/פוט:</b> {cp:.1f}x\n"
                    f"<b>ציון כולל:</b>   {score:.0f}/100\n"
                    f"<b>פונדמנטל:</b>    {fund_str}/100\n"
                    f"<b>קליני:</b>       {clin_str}/100\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"<b>אירוע:</b> {sig.get('event_type','FDA')} בעוד {sig.get('days_until','?')} ימים\n"
                    f"<b>סיבה:</b> {sig.get('reason','')}"
                    f"{flags_str}\n\n"
                    f"⚠️ <b>צא יום לפני ה-FDA</b> — לא להחזיק דרך ההחלטה"
                )
                plain = f"BUY {ticker} @ {entry_str} | FDA in {sig.get('days_until','?')}d | score {score:.0f}"

            send_alert("stock_buy", ticker, plain, telegram_text=tg_text)
            logger.info(f"BUY alert [{scan_path}]: {ticker} score={score:.0f}")

    except Exception as e:
        logger.error(f"_notify_stock_buy_signals failed: {e}")


def _notify_new_trade_ideas(ideas: list):
    """Send Telegram stock WATCH alert when a ticker becomes actionable based on options flow."""
    try:
        from backend.signals.alerter import send_alert

        EW_EMOJI = {"early": "🔵", "optimal": "🟢", "late": "🟠", "avoid": "🔴"}

        for idea in ideas:
            ticker = idea["ticker"]
            em     = idea.get("expected_move")
            ew     = idea.get("entry_window", "")
            score  = idea.get("score", 0)
            cp     = idea.get("call_put_ratio", 1)

            em_str    = f"+{em:.1f}%" if em and cp >= 2 else f"-{em:.1f}%" if em and cp <= 0.7 else f"±{em:.1f}%" if em else "N/A"
            ew_emoji  = EW_EMOJI.get(ew, "")

            tg_text = (
                f"👀 <b>מניה למעקב — {ticker}</b>\n"
                f"<i>{idea.get('company','')}</i>\n\n"
                f"זרימת אופציות חזקה זוהתה לפני אירוע FDA\n\n"
                f"<b>אירוע:</b>    {idea.get('event_type','FDA')} בעוד {idea.get('days_until','?')} ימים\n"
                f"<b>תחזית מניה:</b> {em_str}\n"
                f"<b>חלון כניסה:</b> {ew_emoji} {ew}\n"
                f"<b>סקור:</b>    {score:.0f}/100\n\n"
                f"⚠️ המתן לאישור BUY לפני כניסה"
            )
            plain_msg = f"מניה למעקב: {ticker} | {em_str} | FDA בעוד {idea.get('days_until','?')}d | score {score:.0f}"
            send_alert("trade_idea", ticker, plain_msg, telegram_text=tg_text)
            logger.info(f"Stock watchlist alert sent: {ticker}")

    except Exception as e:
        logger.error(f"_notify_new_trade_ideas failed: {e}")


def run_cleanup():
    """Job: archive past FDA events to historical_results, then remove them."""
    try:
        from datetime import date, timedelta
        from backend.database import SessionLocal
        from backend.models import FdaEvent, OptionsSignal, HistoricalResult
        from backend.data.history_builder import build_historical_result
        from datetime import datetime

        db = SessionLocal()
        today = date.today()

        past_events = db.query(FdaEvent).filter(
            FdaEvent.event_date < today,
            FdaEvent.ticker.isnot(None),
        ).all()

        archived = 0
        for event in past_events:
            existing = db.query(HistoricalResult).filter(
                HistoricalResult.ticker == event.ticker,
                HistoricalResult.event_date == event.event_date,
            ).first()
            if existing:
                continue

            signal = db.query(OptionsSignal).filter(
                OptionsSignal.ticker == event.ticker,
                OptionsSignal.fda_event_id == event.id,
            ).order_by(OptionsSignal.scan_time.desc()).first()

            result_data = build_historical_result(
                ticker=event.ticker,
                company=event.company,
                event_type=event.event_type or "Catalyst",
                drug_name=event.drug_name,
                event_date=event.event_date,
                source=event.source,
                signal=signal,
            )
            db.add(HistoricalResult(**result_data))
            archived += 1

        # Delete events older than 1 day (keep event-day signals fresh for 24h)
        cutoff = today - timedelta(days=1)
        deleted = db.query(FdaEvent).filter(FdaEvent.event_date < cutoff).delete()
        db.commit()
        db.close()
        logger.info(f"Cleanup: archived {archived} events to history, deleted {deleted} old events")

    except Exception as e:
        logger.error(f"Cleanup job failed: {e}")


def run_history_update():
    """Job: update price changes for recent historical results (1d/3d/7d fills in over time)."""
    try:
        from datetime import date, timedelta, datetime
        from backend.database import SessionLocal
        from backend.models import HistoricalResult, AlertLog
        from backend.data.history_builder import (
            get_trading_price_on_or_after, pct_change, classify_outcome
        )

        db = SessionLocal()
        today = date.today()
        cutoff = today - timedelta(days=14)

        results = db.query(HistoricalResult).filter(
            HistoricalResult.event_date >= cutoff,
            HistoricalResult.event_date < today,
        ).all()

        updated = 0
        outcome_notifications = []

        for r in results:
            days_since = (today - r.event_date).days
            changed = False
            just_got_1d = False

            if r.price_1d_after is None and days_since >= 1:
                p = get_trading_price_on_or_after(r.ticker, r.event_date + timedelta(days=1))
                if p:
                    r.price_1d_after = p
                    r.change_1d_pct = pct_change(r.price_before, p)
                    r.outcome = classify_outcome(r.change_1d_pct)
                    changed = True
                    just_got_1d = True

            if r.price_3d_after is None and days_since >= 3:
                p = get_trading_price_on_or_after(r.ticker, r.event_date + timedelta(days=3))
                if p:
                    r.price_3d_after = p
                    r.change_3d_pct = pct_change(r.price_before, p)
                    changed = True

            if r.price_7d_after is None and days_since >= 7:
                p = get_trading_price_on_or_after(r.ticker, r.event_date + timedelta(days=7))
                if p:
                    r.price_7d_after = p
                    r.change_7d_pct = pct_change(r.price_before, p)
                    changed = True

            if changed:
                r.updated_at = datetime.utcnow()
                updated += 1

            # Queue outcome notification when 1d price fills for the first time
            if just_got_1d:
                already = db.query(AlertLog).filter(
                    AlertLog.ticker == r.ticker,
                    AlertLog.alert_type == "outcome_1d",
                    AlertLog.message.like(f"%{r.event_date}%"),
                ).first()
                if not already:
                    outcome_notifications.append({
                        "ticker":       r.ticker,
                        "company":      r.company,
                        "event_type":   r.event_type,
                        "event_date":   r.event_date,
                        "pre_signal":   r.pre_event_alert_level,
                        "pre_score":    r.pre_event_score,
                        "pre_cp":       r.pre_event_call_put_ratio,
                        "price_before": r.price_before,
                        "price_after":  r.price_1d_after,
                        "change_1d":    r.change_1d_pct,
                        "outcome":      r.outcome,
                    })
                    db.add(AlertLog(
                        ticker=r.ticker,
                        alert_type="outcome_1d",
                        score_at_trigger=r.change_1d_pct,
                        message=f"outcome_1d {r.ticker} {r.event_date} change={r.change_1d_pct:.1f}%",
                    ))

        db.commit()
        db.close()
        logger.info(f"History update: refreshed {updated} records")

        if outcome_notifications:
            _notify_outcome_results(outcome_notifications)

    except Exception as e:
        logger.error(f"History update job failed: {e}")


def _notify_outcome_results(outcomes: list):
    """Send Telegram notification with post-event stock outcome vs pre-event signal."""
    try:
        from backend.signals.alerter import send_telegram

        OUTCOME_EMOJI = {
            "strong_up":   "🚀",
            "up":          "📈",
            "neutral":     "➡️",
            "down":        "📉",
            "strong_down": "💥",
        }
        ALERT_EMOJI = {"red": "🔴", "orange": "🟠", "green": "🟢"}

        for o in outcomes:
            chg = o.get("change_1d") or 0
            outcome = o.get("outcome") or "neutral"
            emoji = OUTCOME_EMOJI.get(outcome, "➡️")
            sign = "+" if chg >= 0 else ""
            score = o.get("pre_score")
            alert_lvl = o.get("pre_signal") or "green"
            score_emoji = ALERT_EMOJI.get(alert_lvl, "⚪")

            pb = o.get("price_before")
            pa = o.get("price_after")
            price_str = f"${pb:.2f} → ${pa:.2f}" if pb and pa else "N/A"

            cp = o.get("pre_cp") or 1
            if cp >= 2.0:
                prediction = "↑ עלייה צפויה"
            elif cp <= 0.7:
                prediction = "↓ ירידה צפויה"
            else:
                prediction = "↕ ניטרלי"

            msg = (
                f"{emoji} <b>תוצאת איתות — {o['ticker']}</b>\n"
                f"<i>{o.get('company','')}</i>\n\n"
                f"<b>אירוע:</b>       {o.get('event_type','FDA')} ({o['event_date']})\n"
                f"<b>תחזית לפני:</b>  {prediction}\n"
                f"<b>ציון לפני:</b>   {score_emoji} {score:.0f}/100\n\n"
                f"<b>מחיר:</b>        {price_str}\n"
                f"<b>שינוי יומי:</b>  <b>{sign}{chg:.1f}%</b>\n\n"
            )

            # Was the signal correct?
            bullish_correct = (cp >= 2.0 and chg > 3)
            bearish_correct = (cp <= 0.7 and chg < -3)
            neutral_correct = (abs(cp - 1.0) < 1 and abs(chg) <= 3)
            if bullish_correct or bearish_correct or neutral_correct:
                msg += "✅ <b>האיתות היה מדויק</b>"
            elif (cp >= 2.0 and chg < -3) or (cp <= 0.7 and chg > 3):
                msg += "❌ <b>האיתות לא היה מדויק</b>"
            else:
                msg += "🔘 <b>תוצאה מעורבת</b>"

            send_telegram(msg)
            logger.info(f"Outcome notification sent: {o['ticker']} {sign}{chg:.1f}%")

    except Exception as e:
        logger.error(f"_notify_outcome_results failed: {e}")


def run_alert_check():
    """Job: check for alert conditions every 30 minutes and fire alerts."""
    try:
        from datetime import date, timedelta
        from backend.database import SessionLocal
        from backend.models import OptionsSignal, AlertLog
        from backend.signals.alerter import send_alert

        db = SessionLocal()
        today = date.today()
        cutoff = today + timedelta(days=60)

        # Get latest signal per ticker
        from sqlalchemy import func
        latest_ids = (
            db.query(func.max(OptionsSignal.id))
            .group_by(OptionsSignal.ticker)
            .all()
        )
        latest_signals = (
            db.query(OptionsSignal)
            .filter(OptionsSignal.id.in_([r[0] for r in latest_ids]))
            .all()
        )

        alerted_this_run = set()

        for sig in latest_signals:
            ticker = sig.ticker

            # Get previous signal for comparison
            prev = (
                db.query(OptionsSignal)
                .filter(OptionsSignal.ticker == ticker, OptionsSignal.id < sig.id)
                .order_by(OptionsSignal.scan_time.desc())
                .first()
            )

            # Check if already alerted recently (within 2 hours) for same type
            def already_alerted(atype):
                from datetime import datetime, timedelta
                cutoff_dt = datetime.utcnow() - timedelta(hours=2)
                return db.query(AlertLog).filter(
                    AlertLog.ticker == ticker,
                    AlertLog.alert_type == atype,
                    AlertLog.triggered_at >= cutoff_dt,
                ).first() is not None

            score = sig.composite_score or 0
            prev_score = prev.composite_score if prev else score

            strat = sig.recommended_strategy or "watch"

            # score_spike: composite_score rose 15+ points
            if prev and (score - prev_score) >= 15:
                atype = "score_spike"
                if not already_alerted(atype):
                    plain = f"score_spike: {ticker} score {prev_score:.0f}→{score:.0f} | strategy: {strat}"
                    tg = (
                        f"🚨 <b>SCORE SPIKE — {ticker}</b>\n\n"
                        f"Score jumped <b>{prev_score:.0f} → {score:.0f}</b> (+{score-prev_score:.0f} pts)\n"
                        f"Recommendation: {strat.replace('_',' ').title()}"
                    )
                    send_alert(atype, ticker, plain, db=db, score=score, telegram_text=tg)
                    alerted_this_run.add((ticker, atype))

            # flow_surge: flow_velocity > 80%
            vel = sig.flow_velocity or 0
            if vel > 80:
                atype = "flow_surge"
                if not already_alerted(atype):
                    plain = f"flow_surge: {ticker} flow velocity +{vel:.0f}% | score {score:.0f}"
                    tg = (
                        f"🌊 <b>FLOW SURGE — {ticker}</b>\n\n"
                        f"Premium flow velocity <b>+{vel:.0f}%</b> above 3-scan average\n"
                        f"Score: {score:.0f} | Recommendation: {strat.replace('_',' ').title()}"
                    )
                    send_alert(atype, ticker, plain, db=db, score=score, telegram_text=tg)

            # iv_spike: iv_rank crossed 80 for first time in 5 signals
            ivr = sig.iv_rank or 0
            if ivr >= 80 and prev and (prev.iv_rank or 0) < 80:
                atype = "iv_spike"
                if not already_alerted(atype):
                    plain = f"iv_spike: {ticker} IV rank hit {ivr:.0f} | score {score:.0f}"
                    tg = (
                        f"⚡ <b>IV SPIKE — {ticker}</b>\n\n"
                        f"IV Rank crossed <b>{ivr:.0f}</b> (was {(prev.iv_rank or 0):.0f})\n"
                        f"Score: {score:.0f}"
                    )
                    send_alert(atype, ticker, plain, db=db, score=score, telegram_text=tg)

            # cp_flip: call_put_ratio crossed 3.0 from below
            cp = sig.call_put_ratio or 1.0
            prev_cp = prev.call_put_ratio if prev else cp
            if cp >= 3.0 and (not prev or prev_cp < 3.0):
                atype = "cp_flip"
                if not already_alerted(atype):
                    plain = f"cp_flip: {ticker} C/P ratio {prev_cp:.1f}→{cp:.1f} | score {score:.0f}"
                    tg = (
                        f"🔄 <b>C/P FLIP — {ticker}</b>\n\n"
                        f"Call/Put ratio crossed <b>{cp:.1f}</b> (was {prev_cp:.1f})\n"
                        f"Strong bullish positioning | Score: {score:.0f}"
                    )
                    send_alert(atype, ticker, plain, db=db, score=score, telegram_text=tg)

        db.close()
        logger.info(f"Alert check complete: {len(alerted_this_run)} alerts fired")

    except Exception as e:
        logger.error(f"Alert check job failed: {e}")


def run_penny_scan():
    """
    Job: scan FDA event tickers that are penny/micro-cap stocks (price < $5, no options).
    Uses volume spike + momentum + short squeeze instead of options flow.
    Runs twice daily (pre-market 8:00 AM + mid-day 12:30 PM EST, Mon-Fri).
    """
    try:
        from backend.database import SessionLocal
        from backend.models import AlertLog
        from backend.scrapers.penny_catalyst_scanner import scan_all_penny_catalysts
        from datetime import datetime, timedelta

        db = SessionLocal()
        signals = scan_all_penny_catalysts(db)

        if not signals:
            db.close()
            logger.debug("Penny scan: no BUY signals found")
            return

        cooldown_cutoff = datetime.utcnow() - timedelta(hours=6)
        new_alerts = []

        for sig in signals:
            ticker = sig["ticker"]
            # Cooldown: same ticker once per 6h
            recent = db.query(AlertLog).filter(
                AlertLog.ticker == ticker,
                AlertLog.alert_type == "penny_catalyst",
                AlertLog.triggered_at >= cooldown_cutoff,
            ).first()
            if recent:
                continue

            db.add(AlertLog(
                ticker=ticker,
                alert_type="penny_catalyst",
                score_at_trigger=sig.get("composite_score"),
                message=(
                    f"penny BUY {ticker} score={sig.get('composite_score'):.0f} "
                    f"vol_spike={sig.get('volume_spike'):.1f}x "
                    f"mom={sig.get('momentum_3d_pct'):.1f}% "
                    f"event={sig.get('event_date')}"
                ),
            ))
            new_alerts.append(sig)

        db.commit()
        db.close()

        if new_alerts:
            _notify_penny_signals(new_alerts)
            logger.info(f"Penny scan: {len(new_alerts)} BUY alerts sent")

    except Exception as e:
        logger.error(f"Penny scan job failed: {e}")


def _notify_penny_signals(signals: list):
    """Send Telegram BUY alert for penny/micro-cap catalyst stocks."""
    try:
        from backend.signals.alerter import send_alert

        for sig in signals:
            ticker  = sig["ticker"]
            price   = sig.get("price", 0)
            score   = sig.get("composite_score", 0)
            spike   = sig.get("volume_spike", 0)
            mom     = sig.get("momentum_3d_pct", 0)
            short   = sig.get("short_pct", 0)
            days    = sig.get("days_until", "?")
            risk    = sig.get("risk_level", "HIGH")
            advice  = sig.get("position_advice", "גודל פוזיציה קטן בלבד")
            news_n  = sig.get("news_count_3d", 0)
            event_dt = sig.get("event_date", "")

            risk_emoji = "🔥" if risk == "HIGH" else "⚠️"
            mom_str = f"+{mom:.1f}%" if mom >= 0 else f"{mom:.1f}%"
            news_str = f" | {news_n} ידיעות" if news_n > 0 else ""
            short_str = f" | שורט {short:.0f}% 🔥" if short >= 20 else ""

            tg_text = (
                f"🟢 <b>קנייה: מיקרו-קאפ — {ticker}</b>\n"
                f"<i>{sig.get('company','')}</i>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"<b>מחיר:</b>        ${price:.4f}\n"
                f"<b>נפח יומי:</b>    ×{spike:.1f} מעל ממוצע\n"
                f"<b>מומנטום 3י׳:</b>  {mom_str}{news_str}\n"
                f"{f'<b>שורט:</b>        {short:.0f}%{short_str}{chr(10)}' if short >= 10 else ''}"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"<b>אירוע FDA:</b>   {sig.get('event_type','FDA')} ({event_dt})\n"
                f"<b>ימים לאירוע:</b> {days}\n"
                f"<b>ציון:</b>        {score:.0f}/100\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"<b>סיבה:</b> {sig.get('reason','')}\n\n"
                f"{risk_emoji} <b>סיכון {risk}</b> — {advice}\n"
                f"⚠️ <i>אין אופציות — כניסה דרך המניה בלבד</i>\n"
                f"🚪 <b>יציאה: יום לפני האירוע — לא להחזיק דרך ה-FDA</b>"
            )
            plain = (
                f"PENNY BUY {ticker} ${price:.4f} | vol×{spike:.1f} | "
                f"mom{mom_str} | FDA in {days}d | score {score:.0f}"
            )
            send_alert("penny_catalyst", ticker, plain, telegram_text=tg_text)
            logger.info(f"Penny BUY alert: {ticker} ${price:.4f} vol×{spike:.1f} score={score:.0f}")

    except Exception as e:
        logger.error(f"_notify_penny_signals failed: {e}")


def run_edgar_scan():
    """
    Job: scan EDGAR for recent 8-K FDA filings (every 6h, 24/7).
    Catches micro-cap biotechs that have NO options but can still move 30-100%.
    Sends Telegram alert for positive/neutral filings only (negative = separate alert).
    """
    try:
        from backend.scrapers.edgar_scanner import scan_edgar_fda_filings
        from backend.database import SessionLocal
        from backend.models import FdaEvent, AlertLog
        from backend.signals.alerter import send_telegram
        from datetime import datetime, timedelta

        filings = scan_edgar_fda_filings(lookback_days=1, max_results=30)
        if not filings:
            logger.debug("EDGAR scan: no new FDA filings in last 24h")
            return

        db = SessionLocal()
        cooldown_cutoff = datetime.utcnow() - timedelta(hours=12)
        new_alerts = []

        for f in filings:
            ticker  = f.get("ticker")
            company = f.get("company") or ""
            sentiment = f.get("_sentiment", "neutral")

            # Skip negative filings — bad news, not a BUY setup
            if sentiment == "negative":
                logger.info(f"EDGAR negative: {company} ({ticker}) — skipping alert")
                continue

            # Alert cooldown — don't spam same company
            alert_key = ticker or company[:20]
            recent = db.query(AlertLog).filter(
                AlertLog.ticker == alert_key,
                AlertLog.alert_type == "edgar_filing",
                AlertLog.triggered_at >= cooldown_cutoff,
            ).first()
            if recent:
                continue

            # Add to FdaEvent DB if ticker known and not already present
            if ticker:
                existing = db.query(FdaEvent).filter(
                    FdaEvent.ticker == ticker,
                    FdaEvent.event_date == f["event_date"],
                    FdaEvent.source == "edgar/8-K",
                ).first()
                if not existing:
                    db.add(FdaEvent(
                        ticker=ticker,
                        company=company,
                        event_type="FDA Filing (8-K)",
                        event_date=f["event_date"],
                        source="edgar/8-K",
                    ))

            db.add(AlertLog(
                ticker=alert_key,
                alert_type="edgar_filing",
                score_at_trigger=None,
                message=f"edgar 8-K: {company} {f['event_date']} sentiment={sentiment}",
            ))
            new_alerts.append(f)

        db.commit()
        db.close()

        if new_alerts:
            # Send one batched Telegram message
            lines = ["📄 <b>הגשות 8-K חדשות ל-SEC — FDA</b>\n"]
            for f in new_alerts[:10]:
                t = f.get("ticker") or "—"
                s = {"positive": "🟢", "neutral": "⚪"}.get(f.get("_sentiment", "neutral"), "⚪")
                lines.append(f"{s} <b>{t}</b> — {f.get('company','')}\n   {f.get('event_type','')} ({f.get('event_date','')})")
            msg = "\n\n".join(lines) + "\n\n⚠️ <i>מניות אלו ייתכן שאין להן אופציות — בדוק לפני כניסה</i>"
            send_telegram(msg)
            logger.info(f"EDGAR scan: {len(new_alerts)} new FDA 8-K filings alerted")

    except Exception as e:
        logger.error(f"EDGAR scan job failed: {e}")


def run_learning_update():
    """
    Job: analyze historical outcomes with Claude and update learned weights.
    Runs after history_update (Mon-Fri 9:05 AM) and weekly on Sunday.
    Also scans Telegram digest with learning summary.
    """
    try:
        from backend.database import SessionLocal
        from backend.signals.learning_engine import (
            analyze_outcome_patterns, build_learning_digest
        )
        from backend.signals.alerter import send_telegram

        db = SessionLocal()
        insights = analyze_outcome_patterns(db)
        db.commit()
        db.close()

        if insights:
            msg = build_learning_digest(insights)
            send_telegram(msg)
            logger.info(
                f"Learning update: accuracy={insights.get('accuracy_rate')}% "
                f"confidence={insights.get('confidence')} "
                f"n={insights.get('sample_size')}"
            )
        else:
            logger.info("Learning update: not enough data for pattern analysis yet")

    except Exception as e:
        logger.error(f"Learning update job failed: {e}")


def run_daily_digest():
    """Job: send daily Telegram digest of tickers with FDA events in 1-2 days."""
    try:
        from datetime import date, timedelta
        from backend.database import SessionLocal
        from backend.models import FdaEvent, OptionsSignal
        from backend.signals.alerter import send_telegram
        from sqlalchemy import func

        db = SessionLocal()
        today = date.today()
        cutoff = today + timedelta(days=2)

        events = db.query(FdaEvent).filter(
            FdaEvent.event_date >= today,
            FdaEvent.event_date <= cutoff,
            FdaEvent.ticker.isnot(None),
        ).order_by(FdaEvent.event_date).all()

        if not events:
            db.close()
            logger.info("Daily digest: no events in next 2 days")
            return

        # Get latest signal per ticker
        latest_ids = (
            db.query(func.max(OptionsSignal.id))
            .group_by(OptionsSignal.ticker)
            .all()
        )
        signals = {
            s.ticker: s for s in db.query(OptionsSignal)
            .filter(OptionsSignal.id.in_([r[0] for r in latest_ids]))
            .all()
        }
        db.close()

        SCORE_EMOJI = {"red": "🔴", "orange": "🟠", "green": "🟢"}
        SIGNAL_EMOJI = {"BUY": "🟢", "WATCH": "👀", "AVOID": "🔴"}

        lines = ["📅 <b>אירועי FDA — הבאים 1-2 ימים</b>\n"]
        seen = set()

        for event in events:
            if event.ticker in seen:
                continue
            seen.add(event.ticker)

            days = (event.event_date - today).days
            days_str = "היום" if days == 0 else "מחר" if days == 1 else f"בעוד {days} ימים"
            sig = signals.get(event.ticker)

            if sig:
                score_emoji = SCORE_EMOJI.get(sig.alert_level, "⚪")
                stock_sig   = getattr(sig, "stock_signal", "WATCH") or "WATCH"
                sig_emoji   = SIGNAL_EMOJI.get(stock_sig, "👀")
                entry       = getattr(sig, "entry_price", None)
                stop        = getattr(sig, "stop_loss_price", None)
                em          = sig.expected_move_pct
                cp          = sig.call_put_ratio or 1

                entry_str = f" | כניסה: ${entry:.2f}" if entry else ""
                stop_str  = f" | סטופ: ${stop:.2f}" if stop else ""
                if em:
                    direction = "↑" if cp >= 2 else "↓" if cp <= 0.7 else "↕"
                    em_str = f" | {direction}{em:.1f}%"
                else:
                    em_str = ""

                fund_score = getattr(sig, "fundamental_score", None)
                fund_str = f" | פונד: {fund_score:.0f}" if fund_score is not None else ""

                fund_flags = []
                if getattr(sig, "cash_warning", 0):    fund_flags.append("⚠️מזומן נמוך")
                if getattr(sig, "squeeze_setup", 0):   fund_flags.append("🔥שורט גבוה")
                if getattr(sig, "analyst_bullish", 0): fund_flags.append("📊אנליסטים חיוביים")
                flags_str = " " + " ".join(fund_flags) if fund_flags else ""

                signal_line = f"   {sig_emoji} <b>{stock_sig}</b>{entry_str}{stop_str}{em_str}\n   {score_emoji} סקור: {sig.composite_score:.0f}/100{fund_str}{flags_str}"
            else:
                signal_line = "   ⚪ אין נתוני סיגנל"

            lines.append(
                f"<b>{event.ticker}</b> — {event.company or ''}\n"
                f"   {event.event_type} | {days_str} ({event.event_date})\n"
                f"{signal_line}"
            )

        msg = "\n\n".join(lines)
        send_telegram(msg)
        logger.info(f"Daily digest sent: {len(seen)} tickers")

    except Exception as e:
        logger.error(f"Daily digest job failed: {e}")


def create_scheduler() -> BackgroundScheduler:
    """
    24/7 scheduler with tiered scanning:
      - Every 2h, 24/7    : FDA event scrape (catches EDGAR filings overnight)
      - Every 5 min, 9-17 : Urgent scan — events 0-2 days (most time-sensitive)
      - Every 15 min, 8-20: Focused scan — events 0-7 days (real-time BUY alerts)
      - Every 60 min, 9-16: Full scan — all 60-day events (builds signal history)
      - Every 15 min, 8-20: Alert check (score spikes, flow surges, IV spikes)
      - 8:30 AM daily     : Morning digest
      - 9:00 AM daily     : History price update
      - 2:00 AM daily     : Nightly cleanup
    """
    scheduler = BackgroundScheduler(timezone=EST)

    # ── 24/7: FDA event scrape every 2 hours ──────────────────────────────────
    scheduler.add_job(
        run_fda_scrape,
        trigger=IntervalTrigger(hours=2),
        id="fda_scrape",
        name="FDA Event Scrape (2h, 24/7)",
        replace_existing=True,
        max_instances=1,
    )

    # ── URGENT: 0-2 day events, every 5 min, Mon-Fri 9:00-17:00 EST ──────────
    scheduler.add_job(
        run_urgent_scan,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="9-16",
            minute="*/5",
            timezone=EST,
        ),
        id="urgent_scan",
        name="Urgent Scan 0-2d (5min)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # ── FOCUSED: 0-7 day events, every 15 min, Mon-Fri 8:00-20:00 EST ────────
    scheduler.add_job(
        run_focused_scan,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="8-19",
            minute="*/15",
            timezone=EST,
        ),
        id="focused_scan",
        name="Focused Scan 0-7d (15min)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # ── FULL: all 60-day events, every hour, Mon-Fri 9:00-16:00 EST ──────────
    scheduler.add_job(
        run_options_scan,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="9-15",
            minute="0",
            timezone=EST,
        ),
        id="options_scan",
        name="Full Options Scan 60d (hourly)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # ── ALERT CHECK: score spikes, flow surges — every 15 min 8:00-20:00 EST ─
    scheduler.add_job(
        run_alert_check,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="8-19",
            minute="*/15",
            timezone=EST,
        ),
        id="alert_check",
        name="Alert Check (15min)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # ── MORNING DIGEST: 8:30 AM EST, Mon-Fri ─────────────────────────────────
    scheduler.add_job(
        run_daily_digest,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=8, minute=30,
            timezone=EST,
        ),
        id="daily_digest",
        name="Morning Digest (8:30 EST)",
        replace_existing=True,
    )

    # ── HISTORY UPDATE: 9:00 AM EST ───────────────────────────────────────────
    scheduler.add_job(
        run_history_update,
        trigger=CronTrigger(hour=9, minute=0, timezone=EST),
        id="history_update",
        name="Historical Price Update (9:00 EST)",
        replace_existing=True,
    )

    # ── NIGHTLY CLEANUP: 2:00 AM EST ─────────────────────────────────────────
    scheduler.add_job(
        run_cleanup,
        trigger=CronTrigger(hour=2, minute=0, timezone=EST),
        id="nightly_cleanup",
        name="Nightly Cleanup (2:00 EST)",
        replace_existing=True,
    )

    # ── PENNY CATALYST SCAN: 8:00 AM + 12:30 PM EST Mon-Fri ─────────────────
    # Volume-spike based detection for stocks < $5 with FDA events
    scheduler.add_job(
        run_penny_scan,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="8,12",
            minute="0,30",
            timezone=EST,
        ),
        id="penny_scan",
        name="Penny Catalyst Scan (2x daily)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # ── EDGAR 8-K SCAN: every 6h, 24/7 (catches micro-caps without options) ───
    scheduler.add_job(
        run_edgar_scan,
        trigger=IntervalTrigger(hours=6),
        id="edgar_scan",
        name="EDGAR 8-K FDA Scan (6h, 24/7)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # ── LEARNING UPDATE: Mon-Fri 9:05 AM EST (after history update) ──────────
    # Also runs Sunday 8:00 AM for weekly deep analysis
    scheduler.add_job(
        run_learning_update,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=9, minute=5,
            timezone=EST,
        ),
        id="learning_update_daily",
        name="Learning Update - Daily (9:05 EST)",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        run_learning_update,
        trigger=CronTrigger(
            day_of_week="sun",
            hour=8, minute=0,
            timezone=EST,
        ),
        id="learning_update_weekly",
        name="Learning Update - Weekly (Sun 8:00 EST)",
        replace_existing=True,
        max_instances=1,
    )

    return scheduler
