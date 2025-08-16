# bot.py  — full version (webhook + scheduler + keepalive + rich logging)

import os
import json
import time
import logging
from datetime import datetime, timezone as dt_tz

import requests
from flask import Flask, request, Response
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential

# ===== ENV =====
TOKEN            = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("ADMIN_CHAT_ID", "")
ALERT_CHAT_ID    = os.getenv("ALERT_CHAT_ID", ADMIN_CHAT_ID)
WEBHOOK_URL      = os.getenv("WEBHOOK_URL", "")  # ex: https://stripe-tiger-bot.onrender.com/webhook
HEARTBEAT_SEC    = int(os.getenv("HEARTBEAT_INTERVAL", "900"))
AUTO_START       = os.getenv("AUTO_START", "true").lower() == "true"
PORT             = int(os.getenv("PORT", "10000"))
SELF_URL         = os.getenv("SELF_URL", "")      # ex: https://stripe-tiger-bot.onrender.com
TZ_NAME          = os.getenv("TIMEZONE", "UTC")

# ===== LOGGING =====
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("bot")

# ===== ENGINE =====
# keep your import as you had it
from trade_machine import TradeMachine
engine = TradeMachine(tg_sender=None)  # we'll inject tg_send after def

# ----- Telegram send helper -----
def tg_send(chat_id: str, text: str):
    if not TOKEN or not chat_id:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        if r.status_code != 200:
            logger.error("sendMessage failed: %s | body=%s", r.status_code, r.text)
    except Exception as e:
        logger.exception("Telegram send error: %s", e)

# inject so engine can notify through Telegram if it wants
engine.tg_sender = tg_send

# ===== FLASK =====
app = Flask(__name__)

# ===== SCHEDULER =====
sched = BackgroundScheduler(timezone=TZ_NAME)

def heartbeat():
    ts = datetime.now(dt_tz.utc).isoformat(timespec="seconds")
    tg_send(ALERT_CHAT_ID, f"❤️ heartbeat {ts}")

def trading_cycle():
    try:
        engine.run_cycle()
    except Exception as e:
        logger.exception("Cycle error")
        tg_send(ALERT_CHAT_ID, f"⚠️ Cycle error: {e}")

def keepalive():
    if not SELF_URL:
        return
    try:
        requests.get(f"{SELF_URL}/healthz", timeout=8)
    except Exception:
        # don’t spam logs unless it persists
        pass

def start_jobs():
    # schedule heartbeat & trading loop
    sched.add_job(heartbeat, "interval", seconds=HEARTBEAT_SEC, id="heartbeat", replace_existing=True)
    sched.add_job(trading_cycle, "interval", seconds=engine.poll_seconds, id="trading_cycle", replace_existing=True)
    # light keepalive ping every 5 minutes (optional)
    sched.add_job(keepalive, "interval", seconds=300, id="keepalive", replace_existing=True)
    if not sched.running:
        sched.start()
    logger.info("Scheduler started.")

# ===== Webhook management =====
def _get_wh_info():
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo", timeout=10)
        return r.json()
    except Exception:
        return {}

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=30))
def ensure_webhook():
    if not TOKEN or not WEBHOOK_URL:
        logger.warning("TOKEN or WEBHOOK_URL missing; skipping setWebhook.")
        return
    # Set webhook
    r = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/setWebhook",
        params={"url": WEBHOOK_URL, "drop_pending_updates": True, "allowed_updates": json.dumps(["message","edited_message"])},
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"setWebhook failed: {r.text}")
    info = _get_wh_info()
    logger.info("Webhook set OK. getWebhookInfo=%s", info)

# ===== ROUTES =====
@app.route("/", methods=["GET"])
def root():
    return Response("OK", status=200)

@app.route("/healthz", methods=["GET"])
def healthz():
    return Response("healthy", status=200)

@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True) or {}
    msg = update.get("message") or update.get("edited_message") or {}
    text: str = (msg.get("text") or "").strip()
    chat_id = str(msg.get("chat", {}).get("id") or "") or ADMIN_CHAT_ID

    logger.info("Update: chat=%s | text=%r", chat_id, text)

    if not text:
        return Response("no-text", status=200)

    low = text.lower()

    # ---- commands ----
    if low.startswith("/start"):
        tg_send(chat_id, "🐯 Stripe Tiger bot is live and hunting.")
        return Response("ok", status=200)

    if low.startswith("/status"):
        tg_send(chat_id, engine.status_text())
        return Response("ok", status=200)

    if low.startswith("/mode"):
        parts = low.split()
        if len(parts) == 2 and parts[1] in ("mock", "live"):
            engine.set_mode(parts[1])
            tg_send(chat_id, f"Mode set to {parts[1]}")
        else:
            tg_send(chat_id, "Usage: /mode mock|live")
        return Response("ok", status=200)

    if low.startswith("/pause"):
        engine.pause()
        tg_send(chat_id, "Engine paused")
        return Response("ok", status=200)

    if low.startswith("/resume"):
        engine.resume()
        tg_send(chat_id, "Engine resumed")
        return Response("ok", status=200)

    if low.startswith("/buy"):
        token = text.split(" ", 1)[1].strip() if " " in text else ""
        res = engine.manual_buy(token)
        tg_send(chat_id, res or "Buy attempted.")
        return Response("ok", status=200)

    if low.startswith("/sell"):
        token = text.split(" ", 1)[1].strip() if " " in text else ""
        res = engine.manual_sell(token)
        tg_send(chat_id, res or "Sell attempted.")
        return Response("ok", status=200)

    if low.startswith("/ping"):
        tg_send(chat_id, "pong")
        return Response("ok", status=200)

    # default
    tg_send(chat_id, "Commands: /start /status /mode mock|live /pause /resume /buy <token> /sell <token> /ping")
    return Response("ok", status=200)

# ===== BOOT =====
def boot():
    try:
        ensure_webhook()
    except Exception as e:
        logger.warning("Webhook not set: %s", e)
    start_jobs()
    if AUTO_START:
        engine.resume()

# run once on import (works under gunicorn -w 1)
boot()

if __name__ == "__main__":
    # local run
    app.run(host="0.0.0.0", port=PORT)        
