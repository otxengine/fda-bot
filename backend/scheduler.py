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
        fda_events = scrape_fda_calendar()
        bpw_events = scrape_biopharmawatch()
        bpc_events = scrape_biopharmcatalyst()
        all_events = fda_events + bpw_events + bpc_events

        added = 0
        for event_data in all_events:
            # Deduplicate by ticker + event_date + event_type
            existing = db.query(FdaEvent).filter(
                FdaEvent.event_date == event_data["event_date"],
                FdaEvent.event_type == event_data.get("event_type"),
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


def run_options_scan(force: bool = False):
    """Job: scan options data for all upcoming FDA event tickers."""
    if not force and not is_market_hours():
        logger.debug("Outside market hours - skipping options scan")
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

        # Get events in next 60 days with known tickers
        today = date.today()
        cutoff = today + timedelta(days=60)
        events = db.query(FdaEvent).filter(
            FdaEvent.event_date >= today,
            FdaEvent.event_date <= cutoff,
            FdaEvent.ticker.isnot(None),
        ).all()

        scanned = 0
        new_trade_ideas = []
        new_buy_signals = []

        for event in events:
            if not event.ticker:
                continue

            # Get previous signal to detect new trade ideas
            prev_signal = (
                db.query(OptionsSignal)
                .filter(OptionsSignal.ticker == event.ticker)
                .order_by(OptionsSignal.scan_time.desc())
                .first()
            )
            prev_strategy = prev_signal.recommended_strategy if prev_signal else None

            result = analyze_ticker(
                ticker=event.ticker,
                polygon_client=polygon,
                yfinance_client=yf_client,
                event_date=event.event_date,
                event_type=event.event_type,
                db=db,
                fda_event_id=event.id,
            )
            if result:
                signal = OptionsSignal(**{k: v for k, v in result.items() if not k.startswith("_")})
                db.add(signal)
                scanned += 1

                # Detect new BUY stock signals
                prev_stock_signal = prev_signal.stock_signal if prev_signal else None
                new_stock_signal = result.get("stock_signal")
                if new_stock_signal == "BUY" and prev_stock_signal != "BUY":
                    new_buy_signals.append({
                        "ticker":      event.ticker,
                        "company":     event.company,
                        "event_type":  event.event_type,
                        "days_until":  (event.event_date - today).days,
                        "entry_price": result.get("entry_price"),
                        "stop_loss":   result.get("stop_loss_price"),
                        "target_date": result.get("target_date"),
                        "score":       result.get("composite_score"),
                        "reason":      result.get("stock_signal_reason"),
                        "expected_move": result.get("expected_move_pct"),
                    })

                # Detect newly actionable trade ideas (watch/None → long_call/put/straddle)
                new_strat = result.get("recommended_strategy")
                actionable = {"long_call", "long_put", "long_straddle"}
                if new_strat in actionable and prev_strategy not in actionable:
                    new_trade_ideas.append({
                        "ticker":       event.ticker,
                        "company":      event.company,
                        "strategy":     new_strat,
                        "conviction":   result.get("strategy_conviction"),
                        "rationale":    result.get("strategy_rationale"),
                        "expected_move": result.get("expected_move_pct"),
                        "entry_window": result.get("entry_window"),
                        "score":        result.get("composite_score"),
                        "best_expiry":  result.get("best_expiry"),
                        "days_until":   (event.event_date - today).days,
                        "event_type":   event.event_type,
                        "contract":     result.get("_rec_contract"),
                        "exit":         result.get("_rec_exit"),
                    })

        db.commit()
        db.close()
        logger.info(f"Options scan complete: {scanned} tickers analyzed")

        # Send Telegram alerts for new trade ideas (options)
        if new_trade_ideas:
            _notify_new_trade_ideas(new_trade_ideas)

        # Send Telegram alerts for new BUY stock signals
        if new_buy_signals:
            _notify_stock_buy_signals(new_buy_signals)

    except Exception as e:
        logger.error(f"Options scan job failed: {e}")


def _notify_stock_buy_signals(signals: list):
    """Send Telegram BUY signal alert for stock trades."""
    try:
        from backend.signals.alerter import send_alert

        for sig in signals:
            ticker = sig["ticker"]
            entry  = sig.get("entry_price")
            stop   = sig.get("stop_loss")
            target = sig.get("target_date", "")
            score  = sig.get("score", 0)
            em     = sig.get("expected_move")
            em_str = f"±{em:.1f}%" if em is not None else "N/A"

            entry_str = f"${entry:.2f}" if entry else "market"
            stop_str  = f"${stop:.2f}" if stop else "N/A"
            risk_pct  = f"{((stop-entry)/entry*100):.1f}%" if entry and stop else "-8%"

            tg_text = (
                f"🟢 <b>BUY SIGNAL — {ticker}</b>\n"
                f"<i>{sig.get('company','')}</i>\n\n"
                f"<b>Entry:</b>      {entry_str}\n"
                f"<b>Stop Loss:</b>  {stop_str} ({risk_pct})\n"
                f"<b>Exit Target:</b> {target} (day before FDA)\n"
                f"<b>Exp Move:</b>   {em_str}\n\n"
                f"<b>Event:</b> {sig.get('event_type','FDA')} in {sig.get('days_until','?')}d\n"
                f"<b>Score:</b> {score:.0f}/100\n"
                f"<b>Reason:</b> {sig.get('reason','')}\n\n"
                f"⚠️ Exit <b>before</b> the FDA event — do not hold through binary outcome"
            )
            plain = f"BUY {ticker} @ {entry_str} | Stop {stop_str} | FDA in {sig.get('days_until','?')}d"
            send_alert("stock_buy", ticker, plain, telegram_text=tg_text)
            logger.info(f"Stock BUY alert sent: {ticker}")

    except Exception as e:
        logger.error(f"_notify_stock_buy_signals failed: {e}")


def _notify_new_trade_ideas(ideas: list):
    """Send Telegram message for each newly actionable trade idea."""
    try:
        from backend.signals.alerter import send_alert, send_telegram

        STRAT_EMOJI = {"long_call": "📈", "long_put": "📉", "long_straddle": "↔️"}
        CONV_EMOJI  = {"high": "🔥", "medium": "⚡", "low": "💤"}
        EW_EMOJI    = {"early": "🔵", "optimal": "🟢", "late": "🟠", "avoid": "🔴"}
        STRAT_NAME  = {"long_call": "Long Call", "long_put": "Long Put", "long_straddle": "Long Straddle"}

        for idea in ideas:
            ticker   = idea["ticker"]
            strategy = idea["strategy"]
            conv     = idea["conviction"] or "low"
            em       = idea.get("expected_move")
            ew       = idea.get("entry_window", "")
            score    = idea.get("score", 0)

            em_str = f"±{em:.1f}%" if em is not None else "N/A"

            tg_text = (
                f"{STRAT_EMOJI.get(strategy,'📊')} <b>NEW TRADE IDEA — {ticker}</b>\n"
                f"<i>{idea.get('company','')}</i>\n\n"
                f"<b>Strategy:</b>  {STRAT_NAME.get(strategy, strategy)}\n"
                f"<b>Conviction:</b> {CONV_EMOJI.get(conv,'')} {conv.upper()}\n"
                f"<b>Rationale:</b> {idea.get('rationale','')}\n\n"
                f"<b>Event:</b>     {idea.get('event_type','FDA')} in {idea.get('days_until','?')}d\n"
                f"<b>Expiry:</b>    {idea.get('best_expiry','?')}\n"
                f"<b>Exp Move:</b>  {em_str}\n"
                f"<b>Entry:</b>     {EW_EMOJI.get(ew,'')} {ew.capitalize()}\n"
                f"<b>Score:</b>     {score:.1f}/100\n"
            )
            if idea.get("contract"):
                tg_text += f"\n<b>Trade:</b> {idea['contract']}\n"
            if idea.get("exit"):
                tg_text += f"<b>Exit:</b>  {idea['exit']}\n"

            plain_msg = f"{STRAT_NAME.get(strategy, strategy)} on {ticker} | score {score:.0f} | {em_str}"
            send_alert("trade_idea", ticker, plain_msg, telegram_text=tg_text)
            logger.info(f"Trade idea alert sent: {strategy} on {ticker}")

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
        from backend.models import HistoricalResult
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
        for r in results:
            days_since = (today - r.event_date).days
            changed = False

            if r.price_1d_after is None and days_since >= 1:
                p = get_trading_price_on_or_after(r.ticker, r.event_date + timedelta(days=1))
                if p:
                    r.price_1d_after = p
                    r.change_1d_pct = pct_change(r.price_before, p)
                    r.outcome = classify_outcome(r.change_1d_pct)
                    changed = True

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

        db.commit()
        db.close()
        logger.info(f"History update: refreshed {updated} records")

    except Exception as e:
        logger.error(f"History update job failed: {e}")


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

        SCORE_EMOJI = {
            "red":    "🔴",
            "orange": "🟠",
            "green":  "🟢",
        }
        STRAT_LABEL = {
            "long_call":     "📈 Long Call",
            "long_put":      "📉 Long Put",
            "long_straddle": "↔️ Straddle",
        }

        lines = ["📅 <b>FDA EVENTS — הבאים 1-2 ימים</b>\n"]
        seen = set()

        for event in events:
            if event.ticker in seen:
                continue
            seen.add(event.ticker)

            days = (event.event_date - today).days
            days_str = "מחר" if days == 1 else "היום" if days == 0 else f"בעוד {days} ימים"
            sig = signals.get(event.ticker)

            score_line = ""
            strat_line = ""
            forecast_line = ""

            if sig:
                emoji = SCORE_EMOJI.get(sig.alert_level, "⚪")
                score_line = f"{emoji} סקור: <b>{sig.composite_score:.0f}/100</b>"
                strat = sig.recommended_strategy
                if strat and strat not in ("watch", "avoid"):
                    strat_line = f"\n   {STRAT_LABEL.get(strat, strat)}"
                if sig.expected_move_pct:
                    cp = sig.call_put_ratio or 1
                    direction = "↑" if cp >= 2 else "↓" if cp <= 0.7 else "↕"
                    forecast_line = f" | {direction} ±{sig.expected_move_pct:.1f}%"

            lines.append(
                f"<b>{event.ticker}</b> — {event.company or ''}\n"
                f"   {event.event_type} | {days_str} ({event.event_date})\n"
                f"   {score_line}{forecast_line}{strat_line}"
            )

        msg = "\n\n".join(lines)
        send_telegram(msg)
        logger.info(f"Daily digest sent: {len(seen)} tickers")

    except Exception as e:
        logger.error(f"Daily digest job failed: {e}")


def create_scheduler() -> BackgroundScheduler:
    """Create and configure the APScheduler instance."""
    scheduler = BackgroundScheduler(timezone=EST)

    # Scrape FDA calendar every 6 hours
    scheduler.add_job(
        run_fda_scrape,
        trigger=IntervalTrigger(hours=6),
        id="fda_scrape",
        name="FDA Calendar Scrape",
        replace_existing=True,
    )

    # Scan options every 30 minutes during market hours (Mon-Fri 9:30-16:00 EST)
    scheduler.add_job(
        run_options_scan,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="9-15",
            minute="0,30",
            timezone=EST,
        ),
        id="options_scan",
        name="Options Data Scan",
        replace_existing=True,
    )

    # Nightly cleanup at 2:00 AM EST
    scheduler.add_job(
        run_cleanup,
        trigger=CronTrigger(hour=2, minute=0, timezone=EST),
        id="nightly_cleanup",
        name="Nightly Cleanup",
        replace_existing=True,
    )

    # History price update every morning at 9:00 AM EST
    scheduler.add_job(
        run_history_update,
        trigger=CronTrigger(hour=9, minute=0, timezone=EST),
        id="history_update",
        name="Historical Price Update",
        replace_existing=True,
    )

    # Daily digest at 8:30 AM EST (before market open)
    scheduler.add_job(
        run_daily_digest,
        trigger=CronTrigger(hour=8, minute=30, timezone=EST),
        id="daily_digest",
        name="Daily FDA Digest",
        replace_existing=True,
    )

    # Alert check every 30 minutes during market hours
    scheduler.add_job(
        run_alert_check,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="9-16",
            minute="0,30",
            timezone=EST,
        ),
        id="alert_check",
        name="Alert Condition Check",
        replace_existing=True,
    )

    return scheduler
