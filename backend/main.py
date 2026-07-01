"""
FDA Options Scanner - FastAPI backend
Serves API endpoints and the frontend dashboard.
"""
import os
import logging
from datetime import date, datetime, timedelta
from typing import Optional
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from backend.database import init_db, get_db, SessionLocal
from backend.models import FdaEvent, OptionsSignal, HistoricalResult, AlertLog
from backend.scheduler import (
    create_scheduler, run_fda_scrape, run_options_scan,
    run_history_update, run_cleanup, run_realtime_scan,
)
from backend.data.polygon import PolygonClient
from backend.data.yfinance_client import YFinanceClient
from backend.signals.analyzer import analyze_ticker, compute_composite_score

polygon_client = PolygonClient()


scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler

    # Initialize database
    logger.info("Initializing database...")
    init_db()

    # Run initial FDA scrape on startup if DB is empty
    db = SessionLocal()
    event_count = db.query(FdaEvent).count()
    db.close()

    if event_count == 0:
        logger.info("No events in DB - running initial FDA scrape...")
        run_fda_scrape()
        run_options_scan(force=True)  # bypass market hours on first run

    # Seed historical data on startup if table is empty
    hist_db = SessionLocal()
    hist_count = hist_db.query(HistoricalResult).count()
    hist_db.close()
    if hist_count == 0:
        logger.info("Seeding historical results...")
        import threading
        from backend.data.history_builder import seed_past_14_days
        def _seed():
            seed_db = SessionLocal()
            try:
                seed_past_14_days(seed_db)
            finally:
                seed_db.close()
        threading.Thread(target=_seed, daemon=True).start()

    # Archive any past events that weren't cleaned up yet
    logger.info("Running startup cleanup...")
    run_cleanup()

    # Start background scheduler
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Scheduler started")

    # Start Telegram bot in background thread (if token configured)
    _tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if _tg_token:
        import threading, time, asyncio
        def _run_tg_bot():
            time.sleep(35)  # wait for previous session to expire
            retry_delay = 60
            while True:
                try:
                    from telegram.ext import Application, CommandHandler, MessageHandler, filters
                    from telegram_bot import cmd_start, cmd_signals, cmd_ideas, cmd_status, cmd_help, unknown_cmd

                    async def _bot_main():
                        tg_app = Application.builder().token(_tg_token).build()
                        tg_app.add_handler(CommandHandler("start",   cmd_start))
                        tg_app.add_handler(CommandHandler("signals", cmd_signals))
                        tg_app.add_handler(CommandHandler("ideas",   cmd_ideas))
                        tg_app.add_handler(CommandHandler("status",  cmd_status))
                        tg_app.add_handler(CommandHandler("help",    cmd_help))
                        tg_app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))
                        await tg_app.initialize()
                        await tg_app.start()
                        await tg_app.updater.start_polling(drop_pending_updates=True)
                        logger.info("Telegram bot polling started")
                        # keep running until stopped
                        while True:
                            await asyncio.sleep(60)

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(_bot_main())

                except Exception as e:
                    logger.error(f"Telegram bot error: {e} — retrying in {retry_delay}s")
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 300)
        threading.Thread(target=_run_tg_bot, daemon=True).start()

    yield

    # Shutdown
    if scheduler:
        scheduler.shutdown()
        logger.info("Scheduler stopped")


app = FastAPI(
    title="FDA Options Scanner",
    description="Detects unusual options activity before FDA events",
    version="1.0.0",
    lifespan=lifespan,
)

# ── API Routes ────────────────────────────────────────────────────────────────

@app.get("/api/events")
def get_fda_events(
    days: int = Query(30, description="Events within next N days"),
    db: Session = Depends(get_db),
):
    """List upcoming FDA events."""
    today = date.today()
    cutoff = today + timedelta(days=days)

    events = (
        db.query(FdaEvent)
        .filter(FdaEvent.event_date >= today, FdaEvent.event_date <= cutoff)
        .order_by(FdaEvent.event_date)
        .all()
    )

    result = []
    for e in events:
        days_until = (e.event_date - today).days
        result.append({
            "id": e.id,
            "ticker": e.ticker,
            "company": e.company,
            "event_type": e.event_type,
            "drug_name": e.drug_name,
            "indication": e.indication,
            "event_date": e.event_date.isoformat(),
            "days_until": days_until,
            "source": e.source,
        })

    return {"events": result, "count": len(result)}


@app.get("/api/signals")
def get_signals(
    days: int = Query(30, description="Events within next N days"),
    min_score: float = Query(0, description="Minimum composite score"),
    db: Session = Depends(get_db),
):
    """
    Get latest options signals for all upcoming FDA event tickers.
    Returns one row per ticker, sorted by composite_score descending.
    """
    today = date.today()
    cutoff = today + timedelta(days=days)

    events = (
        db.query(FdaEvent)
        .filter(
            FdaEvent.event_date >= today,
            FdaEvent.event_date <= cutoff,
            FdaEvent.ticker.isnot(None),
        )
        .order_by(FdaEvent.event_date)
        .all()
    )

    result = []
    seen_tickers = set()

    for event in events:
        ticker = event.ticker
        if ticker in seen_tickers:
            continue
        seen_tickers.add(ticker)

        # Get latest signal for this ticker
        signal = (
            db.query(OptionsSignal)
            .filter(OptionsSignal.ticker == ticker)
            .order_by(OptionsSignal.scan_time.desc())
            .first()
        )

        days_until = (event.event_date - today).days

        if signal:
            if signal.composite_score < min_score:
                continue
            row = {
                "ticker": ticker,
                "company": event.company,
                "event_type": event.event_type,
                "event_date": event.event_date.isoformat(),
                "days_until": days_until,
                "signal_score": signal.composite_score,
                "iv_rank": signal.iv_rank,
                "call_put_ratio": signal.call_put_ratio,
                "vol_oi_ratio": signal.vol_oi_ratio,
                "premium_flow": signal.premium_flow,
                "alert_level": signal.alert_level,
                "stock_price": signal.stock_price,
                "scan_time": signal.scan_time.isoformat(),
                # new expiration fields
                "event_pinned_ratio": signal.event_pinned_ratio,
                "expiration_score": signal.expiration_score,
                "best_expiry": signal.best_expiry,
                "dominant_strike_type": signal.dominant_strike_type,
                # new probability fields
                "p_up_5":  signal.p_up_5,
                "p_up_10": signal.p_up_10,
                "p_down_5": signal.p_down_5,
                "p_down_10": signal.p_down_10,
                "p_calibration_n": signal.p_calibration_n,
                "p_confidence": signal.p_confidence,
                # new Phase A fields
                "expected_move_pct":  getattr(signal, "expected_move_pct", None),
                "entry_window":       getattr(signal, "entry_window", None),
                "liquidity_warning":  bool(getattr(signal, "liquidity_warning", 0)),
                "iv_crush_warning":   bool(getattr(signal, "iv_crush_warning", 0)),
                "earnings_overlap":   bool(getattr(signal, "earnings_overlap", 0)),
                "flow_velocity":      getattr(signal, "flow_velocity", 0),
                # new Phase B fields
                "recommended_strategy": getattr(signal, "recommended_strategy", None),
                "strategy_rationale":   getattr(signal, "strategy_rationale", None),
                "strategy_conviction":  getattr(signal, "strategy_conviction", None),
                "fundamental_score":    getattr(signal, "fundamental_score", None),
                "cash_warning":         bool(getattr(signal, "cash_warning", 0)),
                "squeeze_setup":        bool(getattr(signal, "squeeze_setup", 0)),
                "analyst_bullish":      bool(getattr(signal, "analyst_bullish", 0)),
            }
        else:
            # No signal data yet
            if min_score > 0:
                continue
            row = {
                "ticker": ticker,
                "company": event.company,
                "event_type": event.event_type,
                "event_date": event.event_date.isoformat(),
                "days_until": days_until,
                "signal_score": None,
                "iv_rank": None,
                "call_put_ratio": None,
                "vol_oi_ratio": None,
                "premium_flow": None,
                "alert_level": "unknown",
                "stock_price": None,
                "scan_time": None,
            }

        result.append(row)

    # Sort by signal score descending (None values go last)
    result.sort(key=lambda x: x["signal_score"] or -1, reverse=True)

    return {"signals": result, "count": len(result)}


@app.get("/api/ticker/{symbol}")
def get_ticker_detail(symbol: str, db: Session = Depends(get_db)):
    """Get detailed options breakdown for a specific ticker."""
    symbol = symbol.upper()

    events = (
        db.query(FdaEvent)
        .filter(FdaEvent.ticker == symbol, FdaEvent.event_date >= date.today())
        .order_by(FdaEvent.event_date)
        .all()
    )

    latest_signal = (
        db.query(OptionsSignal)
        .filter(OptionsSignal.ticker == symbol)
        .order_by(OptionsSignal.scan_time.desc())
        .first()
    )

    if not events and not latest_signal:
        raise HTTPException(status_code=404, detail=f"No data found for ticker {symbol}")

    today = date.today()
    event_list = [
        {
            "id": e.id,
            "event_type": e.event_type,
            "drug_name": e.drug_name,
            "indication": e.indication,
            "event_date": e.event_date.isoformat(),
            "days_until": (e.event_date - today).days,
            "source": e.source,
        }
        for e in events
    ]

    signal_breakdown = None
    if latest_signal:
        import json as _json
        exp_breakdown = []
        try:
            if latest_signal.expiration_breakdown_json:
                exp_breakdown = _json.loads(latest_signal.expiration_breakdown_json)
        except Exception:
            pass

        signal_breakdown = {
            "composite_score": latest_signal.composite_score,
            "alert_level": latest_signal.alert_level,
            "components": {
                "expiration": {
                    "value": latest_signal.expiration_score,
                    "weight": 35,
                    "description": "Options volume concentration near FDA event date",
                },
                "iv_rank": {
                    "value": latest_signal.iv_rank,
                    "weight": 20,
                    "description": "IV Rank 0-100 (position in 52-week IV range)",
                },
                "call_put_ratio": {
                    "value": latest_signal.call_put_ratio,
                    "weight": 20,
                    "description": "Call Volume / Put Volume (>3 = strong bullish signal)",
                },
                "vol_oi_ratio": {
                    "value": latest_signal.vol_oi_ratio,
                    "weight": 15,
                    "description": "Volume / Open Interest ratio (>1 = unusual activity)",
                },
                "premium_flow": {
                    "value": latest_signal.premium_flow,
                    "weight": 10,
                    "description": "Total call premium flow in USD",
                },
            },
            "probability": {
                "p_up_5":  latest_signal.p_up_5,
                "p_up_10": latest_signal.p_up_10,
                "p_down_5": latest_signal.p_down_5,
                "p_down_10": latest_signal.p_down_10,
                "calibration_n": latest_signal.p_calibration_n,
                "confidence": latest_signal.p_confidence,
            },
            "expiration": {
                "event_pinned_ratio": latest_signal.event_pinned_ratio,
                "expiration_score": latest_signal.expiration_score,
                "best_expiry": latest_signal.best_expiry,
                "dominant_strike_type": latest_signal.dominant_strike_type,
                "breakdown": exp_breakdown,
                "expected_move_pct": getattr(latest_signal, "expected_move_pct", None),
            },
            "trade_recommendation": {
                "strategy":   getattr(latest_signal, "recommended_strategy", None),
                "rationale":  getattr(latest_signal, "strategy_rationale", None),
                "conviction": getattr(latest_signal, "strategy_conviction", None),
                "best_expiry": latest_signal.best_expiry,
            },
            "entry_analysis": {
                "entry_window":      getattr(latest_signal, "entry_window", None),
                "liquidity_warning": bool(getattr(latest_signal, "liquidity_warning", 0)),
                "iv_crush_warning":  bool(getattr(latest_signal, "iv_crush_warning", 0)),
                "earnings_overlap":  bool(getattr(latest_signal, "earnings_overlap", 0)),
                "flow_velocity":     getattr(latest_signal, "flow_velocity", 0),
            },
            "fundamental": {
                "fundamental_score":  getattr(latest_signal, "fundamental_score", None),
                "clinical_score":     getattr(latest_signal, "clinical_score", None),
                "cash_warning":       bool(getattr(latest_signal, "cash_warning", 0)),
                "squeeze_setup":      bool(getattr(latest_signal, "squeeze_setup", 0)),
                "analyst_bullish":    bool(getattr(latest_signal, "analyst_bullish", 0)),
                "trial_risk":         bool(getattr(latest_signal, "trial_risk", 0)),
                "strong_trial":       bool(getattr(latest_signal, "strong_trial", 0)),
            },
            "raw_data": {
                "call_volume": latest_signal.call_volume,
                "put_volume": latest_signal.put_volume,
                "total_volume": latest_signal.total_volume,
                "open_interest": latest_signal.open_interest,
                "implied_volatility": latest_signal.implied_volatility,
                "stock_price": latest_signal.stock_price,
                "market_cap": latest_signal.market_cap,
                "scan_time": latest_signal.scan_time.isoformat(),
            },
        }

    return {
        "ticker": symbol,
        "fda_events": event_list,
        "signal_breakdown": signal_breakdown,
    }


@app.get("/api/history")
def get_history(
    days: int = Query(14, description="Past N days"),
    db: Session = Depends(get_db),
):
    """Get historical FDA events with post-event stock performance."""
    today = date.today()
    cutoff = today - timedelta(days=days)

    results = (
        db.query(HistoricalResult)
        .filter(HistoricalResult.event_date >= cutoff)
        .order_by(HistoricalResult.event_date.desc())
        .all()
    )

    history = []
    for r in results:
        history.append({
            "id": r.id,
            "ticker": r.ticker,
            "company": r.company,
            "event_type": r.event_type,
            "drug_name": r.drug_name,
            "event_date": r.event_date.isoformat(),
            "days_ago": (today - r.event_date).days,
            "outcome": r.outcome,
            "price_before": r.price_before,
            "price_1d_after": r.price_1d_after,
            "price_3d_after": r.price_3d_after,
            "price_7d_after": r.price_7d_after,
            "change_1d_pct": r.change_1d_pct,
            "change_3d_pct": r.change_3d_pct,
            "change_7d_pct": r.change_7d_pct,
            "pre_event_score": r.pre_event_score,
            "pre_event_iv_rank": r.pre_event_iv_rank,
            "pre_event_call_put_ratio": r.pre_event_call_put_ratio,
            "pre_event_vol_oi_ratio": r.pre_event_vol_oi_ratio,
            "pre_event_premium_flow": r.pre_event_premium_flow,
            "pre_event_alert_level": r.pre_event_alert_level,
        })

    return {"history": history, "count": len(history)}


@app.get("/api/calibration")
def get_calibration(db: Session = Depends(get_db)):
    """Win-rate table bucketed by signal score range."""
    from backend.signals.probability import get_calibration_stats
    return {"buckets": get_calibration_stats(db)}


@app.post("/api/seed-history")
def trigger_seed_history():
    """Manually trigger historical data seeding for past 14 days."""
    import threading
    from backend.data.history_builder import seed_past_14_days, seed_demo_history
    def _run():
        seed_db = SessionLocal()
        try:
            n = seed_past_14_days(seed_db)
            if n == 0:
                # No real past events yet - use demo data from price moves
                seed_demo_history(seed_db)
        finally:
            seed_db.close()
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "seeding triggered", "message": "Building historical results in background..."}


@app.post("/api/refresh")
def trigger_refresh():
    """Manually trigger a fresh FDA scrape + options scan + cleanup."""
    import threading
    def _run():
        run_fda_scrape()
        run_options_scan(force=True)
        from backend.scheduler import run_cleanup
        run_cleanup()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"status": "refresh triggered", "message": "Scraping and scanning in background..."}


@app.post("/api/scan-and-alert")
def scan_and_alert():
    """Run a full scan and send BUY alerts for ALL tickers that currently meet criteria."""
    import threading

    def _run():
        try:
            from datetime import date, timedelta
            from backend.database import SessionLocal
            from backend.models import FdaEvent, OptionsSignal
            from backend.data.polygon import PolygonClient
            from backend.data.yfinance_client import YFinanceClient
            from backend.signals.analyzer import analyze_ticker
            from backend.scheduler import _notify_stock_buy_signals

            db = SessionLocal()
            polygon = PolygonClient()
            yf_client = YFinanceClient()

            # First: refresh the event DB with broad scan
            from backend.scrapers.broad_biotech import scan_broad_biotech
            from backend.models import FdaEvent as FdaEventModel
            broad = scan_broad_biotech(iv_rank_threshold=60, max_tickers=150)
            for ev in broad:
                exists = db.query(FdaEventModel).filter(
                    FdaEventModel.ticker == ev["ticker"],
                    FdaEventModel.event_date == ev["event_date"],
                    FdaEventModel.source == "broad_scan/iv",
                ).first()
                if not exists:
                    db.add(FdaEventModel(
                        ticker=ev["ticker"], company=ev["company"],
                        event_type=ev["event_type"], event_date=ev["event_date"],
                        source=ev["source"],
                    ))
            db.commit()

            today = date.today()
            cutoff = today + timedelta(days=60)
            events = db.query(FdaEvent).filter(
                FdaEvent.event_date >= today,
                FdaEvent.event_date <= cutoff,
                FdaEvent.ticker.isnot(None),
            ).all()

            buy_signals = []
            for event in events:
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
                if not result:
                    continue
                signal = OptionsSignal(**{k: v for k, v in result.items() if not k.startswith("_")})
                db.add(signal)

                days_until = (event.event_date - today).days
                # Only alert for events within 0-7 day catalyst window
                # Skip if already alerted in last 4 hours (cooldown)
                if result.get("stock_signal") == "BUY" and 0 <= days_until <= 7:
                    from datetime import datetime, timedelta as td
                    cooldown_cutoff = datetime.utcnow() - td(hours=4)
                    recent_alert = db.query(AlertLog).filter(
                        AlertLog.ticker == event.ticker,
                        AlertLog.alert_type == "stock_buy",
                        AlertLog.triggered_at >= cooldown_cutoff,
                    ).first()
                    if recent_alert:
                        continue
                    buy_signals.append({
                        "ticker":            event.ticker,
                        "company":           event.company,
                        "event_type":        event.event_type,
                        "days_until":        days_until,
                        "entry_price":       result.get("entry_price"),
                        "stop_loss":         result.get("stop_loss_price"),
                        "target_date":       result.get("target_date"),
                        "score":             result.get("composite_score"),
                        "reason":            result.get("stock_signal_reason"),
                        "expected_move":     result.get("expected_move_pct"),
                        "call_put_ratio":    result.get("call_put_ratio"),
                        "fundamental_score": result.get("fundamental_score"),
                        "clinical_score":    result.get("clinical_score"),
                        "analyst_bullish":   result.get("analyst_bullish"),
                        "squeeze_setup":     result.get("squeeze_setup"),
                    })

            db.commit()
            db.close()

            if buy_signals:
                _notify_stock_buy_signals(buy_signals)
                logger.info(f"scan-and-alert: sent {len(buy_signals)} BUY alerts")
            else:
                # Send a summary if nothing qualifies
                from backend.signals.alerter import send_telegram
                week_events = [e for e in events if (e.event_date - today).days <= 7]
                send_telegram(
                    f"🔍 <b>סריקה הושלמה</b>\n\n"
                    f"נסרקו {len(events)} מניות עם אירועי FDA ב-60 ימים הקרובים.\n"
                    f"אירועים בחלון 0-7 ימים: <b>{len(week_events)}</b>\n\n"
                    f"<b>אין כרגע מניות שעומדות בקריטריוני הקנייה</b>\n"
                    f"(score≥50 + C/P≥1.8 + אירוע בחלון 0-7 ימים)"
                )
                logger.info("scan-and-alert: no BUY signals found")

        except Exception as e:
            logger.error(f"scan-and-alert failed: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "scanning", "message": "Full scan running — BUY alerts will be sent to Telegram shortly"}


@app.post("/api/digest")
def trigger_digest():
    """Manually send the daily FDA digest to Telegram."""
    import threading
    from backend.scheduler import run_daily_digest
    threading.Thread(target=run_daily_digest, daemon=True).start()
    return {"status": "digest triggered"}


@app.post("/api/cleanup")
def trigger_cleanup():
    """Manually archive past FDA events to historical results."""
    import threading
    from backend.scheduler import run_cleanup
    threading.Thread(target=run_cleanup, daemon=True).start()
    return {"status": "cleanup triggered", "message": "Archiving past events to historical results..."}


@app.post("/api/history-update")
def trigger_history_update():
    """Manually run history price update + send outcome notifications."""
    import threading
    from backend.scheduler import run_history_update
    threading.Thread(target=run_history_update, daemon=True).start()
    return {"status": "history update triggered", "message": "Fetching post-event prices and sending outcome alerts..."}


@app.get("/api/debug-db")
def debug_db():
    """Debug: show DB path and file existence."""
    import os
    db_path = os.getenv("DB_PATH", "./fda_scanner.db")
    abs_path = os.path.abspath(db_path)
    return {
        "db_path_env": db_path,
        "abs_path": abs_path,
        "file_exists": os.path.exists(abs_path),
        "file_size_bytes": os.path.getsize(abs_path) if os.path.exists(abs_path) else 0,
        "data_dir_exists": os.path.isdir("/data"),
        "data_dir_contents": os.listdir("/data") if os.path.isdir("/data") else [],
    }


@app.get("/api/debug-scrape")
def debug_scrape():
    """Run each scraper and return event counts + samples."""
    from backend.scrapers.fda_calendar import scrape_fda_calendar
    from backend.scrapers.biopharma import scrape_biopharmawatch
    from backend.scrapers.biopharmcatalyst import scrape_biopharmcatalyst
    results = {}
    for name, fn in [("fda_rss", scrape_fda_calendar), ("biopharmawatch", scrape_biopharmawatch), ("biopharmcatalyst", scrape_biopharmcatalyst)]:
        try:
            events = fn()
            results[name] = {
                "count": len(events),
                "sample": [{"ticker": e.get("ticker"), "company": e.get("company","")[:30], "date": str(e.get("event_date")), "type": e.get("event_type")} for e in events[:5]],
            }
        except Exception as ex:
            results[name] = {"error": str(ex), "count": 0}
    return results


@app.get("/api/debug-history")
def debug_history(db: Session = Depends(get_db)):
    """Debug: show raw historical records without date filter."""
    from backend.models import HistoricalResult
    results = db.query(HistoricalResult).order_by(HistoricalResult.id.desc()).limit(10).all()
    return {
        "total_count": db.query(HistoricalResult).count(),
        "records": [
            {
                "id": r.id,
                "ticker": r.ticker,
                "event_date": str(r.event_date),
                "change_1d_pct": r.change_1d_pct,
                "price_before": r.price_before,
                "outcome": r.outcome,
            }
            for r in results
        ]
    }


@app.post("/api/test-outcome")
def test_outcome_notification(db: Session = Depends(get_db)):
    """Inject a demo historical result and send an outcome notification."""
    from datetime import date, timedelta
    from backend.models import HistoricalResult, AlertLog
    from backend.scheduler import _notify_outcome_results

    event_date = date.today() - timedelta(days=2)

    # Remove any existing test record for this ticker/date
    db.query(HistoricalResult).filter(
        HistoricalResult.ticker == "DEMO",
        HistoricalResult.event_date == event_date,
    ).delete()
    db.query(AlertLog).filter(
        AlertLog.ticker == "DEMO",
        AlertLog.alert_type == "outcome_1d",
    ).delete()

    db.add(HistoricalResult(
        ticker="DEMO",
        company="Demo Biotech Inc.",
        event_type="PDUFA",
        drug_name="TestDrug",
        event_date=event_date,
        source="test",
        price_before=18.50,
        price_1d_after=27.30,
        change_1d_pct=47.6,
        outcome="strong_up",
        pre_event_score=72.0,
        pre_event_alert_level="orange",
        pre_event_call_put_ratio=3.8,
    ))
    db.commit()

    _notify_outcome_results([{
        "ticker":       "DEMO",
        "company":      "Demo Biotech Inc.",
        "event_type":   "PDUFA",
        "event_date":   event_date,
        "pre_signal":   "orange",
        "pre_score":    72.0,
        "pre_cp":       3.8,
        "price_before": 18.50,
        "price_after":  27.30,
        "change_1d":    47.6,
        "outcome":      "strong_up",
    }])

    return {"status": "ok", "message": "Demo outcome notification sent to Telegram"}


@app.post("/api/send-outcomes")
def send_all_outcomes(db: Session = Depends(get_db)):
    """Send outcome notifications for all historical results that have price data."""
    from backend.models import HistoricalResult, AlertLog
    from backend.scheduler import _notify_outcome_results

    results = db.query(HistoricalResult).filter(
        HistoricalResult.change_1d_pct.isnot(None),
    ).order_by(HistoricalResult.event_date.desc()).all()

    to_notify = []
    for r in results:
        already = db.query(AlertLog).filter(
            AlertLog.ticker == r.ticker,
            AlertLog.alert_type == "outcome_1d",
            AlertLog.message.like(f"%{r.event_date}%"),
        ).first()
        if not already:
            to_notify.append({
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

    if to_notify:
        import threading
        threading.Thread(target=_notify_outcome_results, args=(to_notify,), daemon=True).start()

    return {"status": "ok", "sending": len(to_notify), "already_sent": len(results) - len(to_notify)}


@app.get("/api/stock-signals")
def get_stock_signals(db: Session = Depends(get_db)):
    """Return latest BUY stock signals (0-7 day FDA window)."""
    today = date.today()
    cutoff = today + timedelta(days=7)

    events = {
        e.ticker: e for e in db.query(FdaEvent).filter(
            FdaEvent.event_date >= today,
            FdaEvent.event_date <= cutoff,
            FdaEvent.ticker.isnot(None),
        ).all()
    }

    from sqlalchemy import func
    latest_ids = (
        db.query(func.max(OptionsSignal.id))
        .group_by(OptionsSignal.ticker)
        .all()
    )
    signals = (
        db.query(OptionsSignal)
        .filter(OptionsSignal.id.in_([r[0] for r in latest_ids]))
        .all()
    )

    results = []
    for sig in signals:
        ev = events.get(sig.ticker)
        days_until = (ev.event_date - today).days if ev else None
        if days_until is None or days_until > 7:
            continue

        results.append({
            "ticker":           sig.ticker,
            "company":          ev.company if ev else None,
            "event_type":       ev.event_type if ev else None,
            "event_date":       ev.event_date.isoformat() if ev else None,
            "days_until":       days_until,
            "stock_signal":     getattr(sig, "stock_signal", "WATCH"),
            "stock_signal_reason": getattr(sig, "stock_signal_reason", ""),
            "entry_price":      getattr(sig, "entry_price", None),
            "stop_loss_price":  getattr(sig, "stop_loss_price", None),
            "target_date":      getattr(sig, "target_date", None),
            "composite_score":  sig.composite_score,
            "expected_move_pct": getattr(sig, "expected_move_pct", None),
            "entry_window":     getattr(sig, "entry_window", None),
            "call_put_ratio":   sig.call_put_ratio,
            "iv_rank":          sig.iv_rank,
            "premium_flow":     sig.premium_flow,
            "liquidity_warning": bool(getattr(sig, "liquidity_warning", 0)),
            "iv_crush_warning":  bool(getattr(sig, "iv_crush_warning", 0)),
        })

    results.sort(key=lambda x: (
        0 if x["stock_signal"] == "BUY" else 1 if x["stock_signal"] == "WATCH" else 2,
        -(x["composite_score"] or 0),
    ))
    return {"signals": results, "count": len(results)}


@app.get("/api/trade-ideas")
def get_trade_ideas(db: Session = Depends(get_db)):
    """
    Return latest actionable trade recommendations (strategy != watch/avoid),
    one per ticker, sorted by conviction then score.
    """
    today = date.today()
    cutoff = today + timedelta(days=60)

    # All upcoming FDA events for context
    events = {
        e.ticker: e for e in db.query(FdaEvent).filter(
            FdaEvent.event_date >= today,
            FdaEvent.event_date <= cutoff,
            FdaEvent.ticker.isnot(None),
        ).all()
    }

    # Latest signal per ticker with an actionable strategy
    from sqlalchemy import func
    latest_ids = (
        db.query(func.max(OptionsSignal.id))
        .filter(OptionsSignal.recommended_strategy.isnot(None))
        .group_by(OptionsSignal.ticker)
        .all()
    )
    signals = (
        db.query(OptionsSignal)
        .filter(
            OptionsSignal.id.in_([r[0] for r in latest_ids]),
            OptionsSignal.recommended_strategy.notin_(["watch", "avoid"]),
        )
        .all()
    )

    conviction_order = {"high": 0, "medium": 1, "low": 2}
    results = []
    for sig in signals:
        ev = events.get(sig.ticker)
        days_until = (ev.event_date - today).days if ev else None
        results.append({
            "ticker":           sig.ticker,
            "company":          ev.company if ev else None,
            "event_type":       ev.event_type if ev else None,
            "event_date":       ev.event_date.isoformat() if ev else None,
            "days_until":       days_until,
            "strategy":         sig.recommended_strategy,
            "conviction":       sig.strategy_conviction,
            "rationale":        sig.strategy_rationale,
            "best_expiry":      sig.best_expiry,
            "expected_move_pct": getattr(sig, "expected_move_pct", None),
            "entry_window":     getattr(sig, "entry_window", None),
            "composite_score":  sig.composite_score,
            "iv_rank":          sig.iv_rank,
            "call_put_ratio":   sig.call_put_ratio,
            "liquidity_warning": bool(getattr(sig, "liquidity_warning", 0)),
            "iv_crush_warning":  bool(getattr(sig, "iv_crush_warning", 0)),
            "earnings_overlap":  bool(getattr(sig, "earnings_overlap", 0)),
        })

    results.sort(key=lambda x: (
        conviction_order.get(x["conviction"] or "low", 2),
        -(x["composite_score"] or 0),
    ))

    return {"ideas": results, "count": len(results)}


@app.get("/api/alerts")
def get_alerts(
    unread: bool = Query(False, description="Only unacknowledged alerts"),
    db: Session = Depends(get_db),
):
    """Get recent alert log entries."""
    q = db.query(AlertLog)
    if unread:
        q = q.filter(AlertLog.acknowledged == 0)
    alerts = q.order_by(AlertLog.triggered_at.desc()).limit(100).all()
    return {
        "alerts": [
            {
                "id":           a.id,
                "ticker":       a.ticker,
                "alert_type":   a.alert_type,
                "triggered_at": a.triggered_at.isoformat(),
                "score":        a.score_at_trigger,
                "message":      a.message,
                "acknowledged": bool(a.acknowledged),
            }
            for a in alerts
        ],
        "count": len(alerts),
        "unread_count": db.query(AlertLog).filter(AlertLog.acknowledged == 0).count(),
    }


@app.post("/api/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, db: Session = Depends(get_db)):
    """Mark an alert as acknowledged."""
    alert = db.query(AlertLog).filter(AlertLog.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.acknowledged = 1
    db.commit()
    return {"status": "ok"}


@app.post("/api/alerts/acknowledge-all")
def acknowledge_all_alerts(db: Session = Depends(get_db)):
    """Mark all alerts as acknowledged."""
    db.query(AlertLog).filter(AlertLog.acknowledged == 0).update({"acknowledged": 1})
    db.commit()
    return {"status": "ok"}


@app.get("/api/status")
def get_status(db: Session = Depends(get_db)):
    """System status, scan schedule, and stats."""
    today = date.today()
    total_events = db.query(FdaEvent).filter(FdaEvent.event_date >= today).count()
    week_events  = db.query(FdaEvent).filter(
        FdaEvent.event_date >= today,
        FdaEvent.event_date <= today + timedelta(days=7),
        FdaEvent.ticker.isnot(None),
    ).count()
    total_signals = db.query(OptionsSignal).count()
    latest_scan = db.query(OptionsSignal).order_by(OptionsSignal.scan_time.desc()).first()

    # Next scheduled jobs
    jobs_info = []
    if scheduler:
        for job in scheduler.get_jobs():
            next_run = job.next_run_time
            jobs_info.append({
                "id":       job.id,
                "name":     job.name,
                "next_run": next_run.isoformat() if next_run else None,
            })

    return {
        "status":             "running",
        "upcoming_events":    total_events,
        "events_next_7d":     week_events,
        "total_signals":      total_signals,
        "last_scan":          latest_scan.scan_time.isoformat() if latest_scan else None,
        "polygon_configured": bool(os.getenv("POLYGON_API_KEY")),
        "historical_records": db.query(HistoricalResult).count(),
        "scheduled_jobs":     jobs_info,
    }


# ── Static Frontend ───────────────────────────────────────────────────────────

frontend_dir = Path(__file__).parent.parent / "frontend"

if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    @app.get("/")
    def serve_dashboard():
        return FileResponse(str(frontend_dir / "index.html"))
else:
    @app.get("/")
    def root():
        return {"message": "FDA Options Scanner API running. Frontend not found."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
