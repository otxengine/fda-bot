"""
FDA Options Scanner — Telegram Bot

Setup:
  1. Create a bot via @BotFather → get TELEGRAM_BOT_TOKEN
  2. Send /start to the bot, then get your chat_id via /getUpdates or @userinfobot
  3. Add to .env:
       TELEGRAM_BOT_TOKEN=your_token
       TELEGRAM_CHAT_ID=your_chat_id
  4. Run: python telegram_bot.py

Commands:
  /start   — welcome message
  /signals — current top signals
  /ideas   — trade recommendations
  /status  — system status
  /help    — command list
"""
import os
import sys
import asyncio
import logging
import requests as _requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
API_URL = os.getenv("SCANNER_API_URL", "http://localhost:8000")

# ── Telegram send helper ───────────────────────────────────────────────────────

def tg_send(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the configured chat. Returns True on success."""
    if not TOKEN or not CHAT_ID:
        logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        return False
    try:
        r = _requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": parse_mode,
                  "disable_web_page_preview": True},
            timeout=10,
        )
        if not r.ok:
            logger.error(f"Telegram API error: {r.status_code} {r.text}")
        return r.ok
    except Exception as e:
        logger.error(f"tg_send failed: {e}")
        return False

# ── Formatters ─────────────────────────────────────────────────────────────────

STRATEGY_EMOJI = {
    "long_call":     "📈",
    "long_put":      "📉",
    "long_straddle": "↔️",
    "watch":         "👀",
    "avoid":         "🚫",
}

CONVICTION_EMOJI = {
    "high":   "🔥",
    "medium": "⚡",
    "low":    "💤",
}

ENTRY_EMOJI = {
    "early":   "🔵",
    "optimal": "🟢",
    "late":    "🟠",
    "avoid":   "🔴",
}

def fmt_money(n):
    if n is None: return "—"
    if n >= 1_000_000: return f"${n/1_000_000:.1f}M"
    if n >= 1_000:     return f"${n/1_000:.0f}K"
    return f"${n:.0f}"

def fmt_pct(p):
    if p is None: return "—"
    return f"{p*100:.0f}%"


def format_trade_alert(ticker, company, strategy, conviction, rationale,
                       expected_move, entry_window, score, best_expiry,
                       days_until, event_type, alert_type="trade"):
    """Rich Telegram message for a trade recommendation."""
    s_emoji  = STRATEGY_EMOJI.get(strategy, "📊")
    c_emoji  = CONVICTION_EMOJI.get(conviction, "")
    ew_emoji = ENTRY_EMOJI.get(entry_window, "")
    strat_name = {
        "long_call":     "Long Call",
        "long_put":      "Long Put",
        "long_straddle": "Long Straddle",
    }.get(strategy, strategy)

    em_str = f"±{expected_move:.1f}%" if expected_move is not None else "N/A"
    days_str = f"{days_until}d" if days_until is not None else "?"

    lines = [
        f"{s_emoji} <b>TRADE IDEA — {ticker}</b>",
        f"<i>{company or ''}</i>",
        f"",
        f"<b>Strategy:</b>  {strat_name}",
        f"<b>Conviction:</b> {c_emoji} {(conviction or '').upper()}",
        f"<b>Rationale:</b> {rationale or ''}",
        f"",
        f"<b>Event:</b>      {event_type or 'FDA Event'} in {days_str}",
        f"<b>Expiry:</b>     {best_expiry or 'N/A'}",
        f"<b>Exp Move:</b>   {em_str}",
        f"<b>Entry Window:</b> {ew_emoji} {(entry_window or '').capitalize()}",
        f"<b>Signal Score:</b> {score:.1f}/100",
    ]
    return "\n".join(lines)


def format_score_spike(ticker, prev_score, new_score, strategy, conviction, days_until, event_type):
    s_emoji = STRATEGY_EMOJI.get(strategy, "📊")
    strat_name = {
        "long_call": "Long Call", "long_put": "Long Put",
        "long_straddle": "Long Straddle",
    }.get(strategy, strategy or "Watch")
    lines = [
        f"🚨 <b>SCORE SPIKE — {ticker}</b>",
        f"",
        f"Score jumped <b>{prev_score:.0f} → {new_score:.0f}</b> (+{new_score-prev_score:.0f})",
        f"Event: {event_type or 'FDA'} in {days_until}d",
        f"Recommendation: {s_emoji} {strat_name}",
    ]
    return "\n".join(lines)


def format_alert_message(alert_type, ticker, message, score):
    emoji_map = {
        "score_spike":    "🚨",
        "flow_surge":     "🌊",
        "iv_spike":       "⚡",
        "cp_flip":        "🔄",
        "event_imminent": "⏰",
    }
    emoji = emoji_map.get(alert_type, "⚠️")
    label = alert_type.replace("_", " ").upper()
    lines = [
        f"{emoji} <b>{label} — {ticker}</b>",
        f"",
        message,
    ]
    if score is not None:
        lines.append(f"\n<b>Score:</b> {score:.1f}")
    return "\n".join(lines)

# ── API helpers ────────────────────────────────────────────────────────────────

def api_get(path: str) -> dict:
    try:
        r = _requests.get(f"{API_URL}{path}", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"API call failed {path}: {e}")
        return {}


def build_signals_message(days=30):
    data = api_get(f"/api/signals?days={days}&min_score=0")
    signals = data.get("signals", [])
    if not signals:
        return "No upcoming FDA signals found."

    lines = [f"📡 <b>FDA Options Signals</b> (next {days}d)\n"]
    for s in signals[:8]:  # max 8 in telegram
        score = s.get("signal_score")
        score_str = f"{score:.0f}" if score is not None else "—"
        alert = s.get("alert_level", "green")
        dot = "🔴" if alert == "red" else "🟠" if alert == "orange" else "🟢"
        ew = s.get("entry_window", "")
        ew_e = ENTRY_EMOJI.get(ew, "")
        em = s.get("expected_move_pct")
        em_str = f"±{em:.1f}%" if em is not None else ""
        strat = s.get("recommended_strategy", "")
        s_e = STRATEGY_EMOJI.get(strat, "")
        lines.append(
            f"{dot} <b>{s['ticker']}</b> {s_e}  score={score_str}  "
            f"{ew_e}{ew}  {em_str}  {s['days_until']}d"
        )
    return "\n".join(lines)


def build_ideas_message():
    data = api_get("/api/trade-ideas")
    ideas = data.get("ideas", [])
    if not ideas:
        return "No actionable trade ideas right now.\nSignals need score ≥55 with directional flow."

    lines = ["💡 <b>Trade Ideas</b>\n"]
    for idea in ideas[:5]:
        s_e  = STRATEGY_EMOJI.get(idea["strategy"], "📊")
        c_e  = CONVICTION_EMOJI.get(idea["conviction"], "")
        ew_e = ENTRY_EMOJI.get(idea.get("entry_window", ""), "")
        em   = idea.get("expected_move_pct")
        em_str = f"±{em:.1f}%" if em else ""

        strat_name = {"long_call": "Long Call", "long_put": "Long Put",
                      "long_straddle": "Straddle"}.get(idea["strategy"], idea["strategy"])

        lines.append(
            f"{s_e} <b>{idea['ticker']}</b> — {strat_name} {c_e}\n"
            f"   {idea.get('rationale','')}\n"
            f"   Expiry: {idea.get('best_expiry','?')}  Move: {em_str}  Entry: {ew_e}{idea.get('entry_window','')}"
        )
    return "\n\n".join(lines)


def build_status_message():
    data = api_get("/api/status")
    if not data:
        return "Could not reach scanner API."
    return (
        f"⚙️ <b>FDA Scanner Status</b>\n\n"
        f"Status:    {'✅ Running' if data.get('status') == 'running' else '❌ Error'}\n"
        f"Events:    {data.get('upcoming_events', '?')} upcoming\n"
        f"Signals:   {data.get('total_signals', '?')} total\n"
        f"History:   {data.get('historical_records', '?')} records\n"
        f"Last Scan: {(data.get('last_scan') or 'never')[:19].replace('T', ' ')}\n"
        f"Polygon:   {'✅' if data.get('polygon_configured') else '❌ not configured'}"
    )

# ── Bot application ────────────────────────────────────────────────────────────

async def cmd_start(update, context):
    await update.message.reply_text(
        "👋 <b>FDA Options Scanner Bot</b>\n\n"
        "אני שולח אלרטים אוטומטיים כשמתגלות הזדמנויות מסחר.\n\n"
        "פקודות זמינות:\n"
        "/signals — סיגנלים נוכחיים\n"
        "/ideas   — המלצות עסקאות\n"
        "/status  — סטטוס מערכת\n"
        "/help    — עזרה",
        parse_mode="HTML",
    )

async def cmd_signals(update, context):
    await update.message.reply_text(build_signals_message(), parse_mode="HTML")

async def cmd_ideas(update, context):
    await update.message.reply_text(build_ideas_message(), parse_mode="HTML")

async def cmd_status(update, context):
    await update.message.reply_text(build_status_message(), parse_mode="HTML")

async def cmd_help(update, context):
    await update.message.reply_text(
        "📖 <b>פקודות</b>\n\n"
        "/signals — רשימת סיגנלים לפי סקור\n"
        "/ideas   — המלצות Long Call / Put / Straddle\n"
        "/status  — מצב השרת\n\n"
        "אלרטים אוטומטיים נשלחים כש:\n"
        "• סקור קופץ 15+ נקודות\n"
        "• flow velocity > 80%\n"
        "• IV rank חוצה 80\n"
        "• C/P ratio חוצה 3.0\n"
        "• המלצת עסקה חדשה (high/medium conviction)",
        parse_mode="HTML",
    )

async def unknown_cmd(update, context):
    await update.message.reply_text("פקודה לא מוכרת. נסה /help")


def run_bot():
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set in .env")
        sys.exit(1)

    from telegram.ext import Application, CommandHandler, MessageHandler, filters

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("ideas",   cmd_ideas))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))

    logger.info("Telegram bot starting (polling)...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run_bot()
