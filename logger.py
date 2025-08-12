import os
import json
import logging
from datetime import datetime
import requests

# --- Env ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("ALERT_CHAT_ID") or os.getenv("CHAT_ID")
PARSE_MODE = os.getenv("TELEGRAM_PARSE_MODE", "Markdown")

LOG_TO_TELEGRAM = (os.getenv("LOG_TO_TELEGRAM", "true").lower() == "true")
LOG_TO_FILE     = (os.getenv("LOG_TO_FILE", "true").lower() == "true")
LOG_FILE_NAME   = os.getenv("LOG_FILE_NAME", "trade_log.jsonl")
LOG_LEVEL       = os.getenv("LOG_LEVEL", "INFO").upper()

# --- Python logger setup (stdout) ---
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
pylog = logging.getLogger("stripe-tiger")

def _send_telegram(text: str):
    """Fire-and-forget Telegram message (no raise)."""
    if not (LOG_TO_TELEGRAM and BOT_TOKEN and CHAT_ID):
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": text, "parse_mode": PARSE_MODE}
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        pylog.debug(f"Telegram send failed: {e}")

def _append_file(payload: dict):
    """Append
