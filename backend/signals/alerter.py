"""
Alert delivery: saves to AlertLog table, POSTs to webhook, and sends Telegram.
.env vars:
  ALERT_WEBHOOK_URL   — Slack/Discord compatible webhook (optional)
  TELEGRAM_BOT_TOKEN  — Telegram bot token
  TELEGRAM_CHAT_ID    — Telegram chat/user ID to send alerts to
"""
import os
import logging

logger = logging.getLogger(__name__)


def send_telegram(text: str) -> bool:
    """Send a plain text message to Telegram. Returns True on success."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=10,
        )
        return r.ok
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def _format_alert_text(alert_type: str, ticker: str, message: str, score: float = None) -> str:
    emoji_map = {
        "score_spike":    "🚨",
        "flow_surge":     "🌊",
        "iv_spike":       "⚡",
        "cp_flip":        "🔄",
        "event_imminent": "⏰",
        "trade_idea":     "💡",
    }
    emoji = emoji_map.get(alert_type, "⚠️")
    label = alert_type.replace("_", " ").upper()
    score_line = f"\n<b>Score:</b> {score:.1f}" if score is not None else ""
    return f"{emoji} <b>{label} — {ticker}</b>\n\n{message}{score_line}"


def send_alert(alert_type: str, ticker: str, message: str, db=None,
               score: float = None, telegram_text: str = None):
    """
    Save alert to DB, deliver via webhook, and send to Telegram.
    telegram_text: optional rich HTML override for Telegram message.
    """
    # 1. Persist to DB
    if db:
        try:
            from backend.models import AlertLog
            log = AlertLog(
                ticker=ticker,
                alert_type=alert_type,
                message=message,
                score_at_trigger=score,
            )
            db.add(log)
            db.commit()
        except Exception as e:
            logger.error(f"Failed to save alert to DB: {e}")

    # 2. Webhook (Slack/Discord)
    webhook_url = os.getenv("ALERT_WEBHOOK_URL")
    if webhook_url:
        try:
            import requests
            requests.post(webhook_url, json={"text": message}, timeout=5)
            logger.info(f"Webhook sent: {alert_type} for {ticker}")
        except Exception as e:
            logger.error(f"Webhook delivery failed: {e}")

    # 3. Telegram
    tg_text = telegram_text or _format_alert_text(alert_type, ticker, message, score)
    if send_telegram(tg_text):
        logger.info(f"Telegram alert sent: {alert_type} for {ticker}")
