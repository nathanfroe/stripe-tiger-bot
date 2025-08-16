import os
import json
import logging
from datetime import datetime
from flask import Flask, request, Response
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential
import requests

from trademachine import TradeMachine  # keep exact import

# ======= ENV =======
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("ADMIN_CHAT_ID", "")
ALERT_CHAT_ID = os.getenv("ALERT_CHAT_ID", ADMIN_CHAT_ID)
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "900"))  # seconds
AUTO_START = (os.getenv("AUTO_START", "true").lower() == "true")
PORT = int(os.getenv("PORT", "10000"))
TIMEZONE = os.getenv("TIMEZONE", "UTC")
SELF_URL = os.getenv("SELF_URL", "").rstrip("/")  # <-- NEW: optional keepalive target

# ======= LOGGING =======
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s | %(levelname)s | %(message)s',
)
logger = logging.getLogger("bot")

# ======= TELEGRAM =======
def tg_send(chat_id: str, text: str):
    if not TOKEN or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"Telegram send error: {e}")

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=30))
def ensure_webhook():
    if not TOKEN or not WEBHOOK_URL:
        logger.warning("TOKEN or WEBHOOK_URL missing; skipping setWebhook")
        return
    r = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/setWebhook",
        params={"url": WEBHOOK_URL},
        timeout=10,
    )
    if r.status_code != 200:
        raise RuntimeError(f"setWebhook failed: {r.text}")
    logger.info("Webhook set OK.")

# ======= APP / ENGINE / SCHED =======
app = Flask(__name__)
engine = TradeMachine(tg_sender=tg_send)

sched = BackgroundScheduler(timezone=TIMEZONE)

def heartbeat():
    try:
        tg_send(ALERT_CHAT_ID, f"💓 {datetime.utcnow().isoformat(timespec='seconds')}Z")
    except Exception as e:
        logger.warning(f"Heartbeat error: {e}")

def trading_cycle():
    try:
        engine.run_cycle()
    except Exception as e:
        logger.exception("cycle")
        tg_send(ALERT_CHAT_ID, f"⚠️ Cycle error: {e}")

# ---- NEW: tiny self-keepalive ping to prevent Render autosleep ----
def keepalive():
    if not SELF_URL:
        return
    try:
        requests.get(f"{SELF_URL}/healthz", timeout=5)
        logger.info("Keepalive ping OK")
    except Exception as e:
        logger.warning(f"Keepalive ping failed: {e}")

def start_scheduled_jobs():
    # heartbeat
    sched.add_job(heartbeat, "interval", seconds=HEARTBEAT_INTERVAL, id="heartbeat")
    # trading loop (respect engine.poll_seconds if provided)
    poll_secs = getattr(engine, "poll_seconds", 60)
    sched.add_job(trading_cycle, "interval", seconds=poll_secs, id="trading_loop")
    # keepalive every 8 minutes if SELF_URL is set
    if SELF_URL:
        sched.add_job(keepalive, "interval", seconds=480, id="keepalive")
    sched.start()
    logger.info("Scheduler started.")

# ======= ROUTES =======
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
    text = (msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id", ""))

    if not text:
        return Response("no-text", status=200)

    low = text.lower()

    if low == "/status":
        tg_send(chat_id or ADMIN_CHAT_ID, engine.status_text())
    elif low.startswith("/mode"):
        parts = low.split()
        if len(parts) >= 2 and parts[1] in ("mock", "live"):
            engine.set_mode(parts[1])
            tg_send(chat_id or ADMIN_CHAT_ID, f"Mode set to {parts[1]}")
        else:
            tg_send(chat_id or ADMIN_CHAT_ID, "Usage: /mode mock|live")
    elif low == "/pause":
        engine.pause()
        tg_send(chat_id or ADMIN_CHAT_ID, "Engine paused")
    elif low == "/resume":
        engine.resume()
        tg_send(chat_id or ADMIN_CHAT_ID, "Engine resume requested")
    elif low.startswith("/buy "):
        token = text.split(maxsplit=1)[1]
        res = engine.manual_buy(token)
        tg_send(chat_id or ADMIN_CHAT_ID, res)
    elif low.startswith("/sell "):
        token = text.split(maxsplit=1)[1]
        res = engine.manual_sell(token)
        tg_send(chat_id or ADMIN_CHAT_ID, res)
    else:
        tg_send(chat_id or ADMIN_CHAT_ID,
               "Commands: /status, /mode mock|live, /pause, /resume, /buy <token>, /sell <token>")

    return Response("ok", status=200)

# ======= BOOT =======
def boot():
    try:
        ensure_webhook()
    except Exception as e:
        logger.warning(f"Webhook not set: {e}")

    start_scheduled_jobs()
    if AUTO_START:
        engine.resume()

# Run boot at import (works under gunicorn -w 1 with gthread)
boot()

# Local dev path if ever needed
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
