# bot.py — full version (webhook + APScheduler + keepalive + robust logging + /positions + /pnl)

import os
import json
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
WEBHOOK_URL      = os.getenv("WEBHOOK_URL", "")       # e.g. https://stripe-tiger-bot.onrender.com/webhook
HEARTBEAT_SEC    = int(os.getenv("HEARTBEAT_INTERVAL", "900"))
AUTO_START       = os.getenv("AUTO_START", "true").lower() == "true"
PORT             = int(os.getenv("PORT", "10000"))
SELF_URL         = os.getenv("SELF_URL", "")          # e.g. https://stripe-tiger-bot.onrender.com
TZ_NAME          = os.getenv("TIMEZONE", "UTC")

# ===== LOGGING =====
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("bot")

# ===== Telegram send helper =====
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

# ===== ENGINE =====
from trademachine import TradeMachine

# pass tg_sender required by TradeMachine.__init__
engine = TradeMachine(tg_sender=tg_send)

# (Optional compatibility: if engine supports setter we still wire it, no harm)
try:
    if hasattr(engine, "set_sender"):
        engine.set_sender(tg_send)
    else:
        setattr(engine, "tg_sender", tg_send)
except Exception as e:
    logger.warning("Could not attach Telegram sender to engine: %s", e)

# ===== FLASK APP =====
app = Flask(__name__)

# ===== SCHEDULER =====
sched = BackgroundScheduler(timezone=TZ_NAME)

def heartbeat():
    ts = datetime.now(dt_tz.utc).isoformat(timespec="seconds")
    try:
        tg_send(ALERT_CHAT_ID, f"❤️ heartbeat {ts}")
    except Exception as e:
        logger.warning("Heartbeat send failed: %s", e)

def trading_cycle():
    try:
        if hasattr(engine, "run_cycle"):
            engine.run_cycle()
        elif hasattr(engine, "run"):
            engine.run()
        else:
            tg_send(ALERT_CHAT_ID, "⚠️ Engine has no run/run_cycle method.")
    except Exception as e:
        logger.exception("Cycle error")
        tg_send(ALERT_CHAT_ID, f"⚠️ Cycle error: {e}")

def keepalive():
    if not SELF_URL:
        return
    try:
        requests.get(f"{SELF_URL}/healthz", timeout=8)
        logger.info("Keepalive ping OK")
    except Exception:
        pass

def start_jobs():
    sched.add_job(heartbeat, "interval", seconds=HEARTBEAT_SEC, id="heartbeat", replace_existing=True)
    poll_secs = getattr(engine, "poll_seconds", 60)
    sched.add_job(trading_cycle, "interval", seconds=poll_secs, id="trading_cycle", replace_existing=True)
    if SELF_URL:
        sched.add_job(keepalive, "interval", seconds=300, id="keepalive", replace_existing=True)

    if not sched.running:
        sched.start()
    logger.info("Scheduler started.")

# ===== WEBHOOK MGMT =====
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
    r = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/setWebhook",
        params={
            "url": WEBHOOK_URL,
            "drop_pending_updates": True,
            "allowed_updates": json.dumps(["message", "edited_message"])
        },
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

# self-test endpoint to simulate Telegram POST quickly
@app.route("/__selftest", methods=["POST"])
def __selftest():
    """POST JSON: {"chat_id": "<id>", "text": "/ping"} to test handler end-to-end on Render."""
    data = request.get_json(silent=True) or {}
    fake = {
        "message": {
            "chat": {"id": data.get("chat_id", ADMIN_CHAT_ID)},
            "text": data.get("text", "/ping")
        }
    }
    with app.test_request_context("/webhook", method="POST", json=fake):
        return webhook()

@app.route("/webhook", methods=["POST"])
def webhook():
    # log headers + compact payload so we can see hits in Render logs
    try:
        logger.info("Webhook headers: %s", dict(request.headers))
    except Exception:
        pass

    update = request.get_json(silent=True) or {}
    try:
        logger.info("Webhook payload keys: %s", list(update.keys()))
    except Exception:
        pass

    msg = update.get("message") or update.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id") or "") or ADMIN_CHAT_ID

    logger.info("Update: chat=%s | text=%r", chat_id, text)

    if not text:
        return Response("no-text", status=200)

    low = text.lower()

    # -------- commands --------
    if low.startswith("/start"):
        tg_send(chat_id, "🐯 Stripe Tiger bot is live.")
        return Response("ok", status=200)

    if low.startswith("/status"):
        try:
            if hasattr(engine, "status_text"):
                tg_send(chat_id, engine.status_text())
            else:
                tg_send(chat_id, "status_text() not implemented in engine.")
        except Exception as e:
            logger.exception("status")
            tg_send(chat_id, f"Status error: {e}")
        return Response("ok", status=200)

    # NEW: positions snapshot
    if low.startswith("/positions"):
        try:
            if hasattr(engine, "get_positions"):
                pos = engine.get_positions()
                if not pos:
                    tg_send(chat_id, "No open positions.")
                else:
                    lines = []
                    for p in pos:
                        lines.append(
                            f"{p.get('token')[:8]}… on {p.get('chain','')} | "
                            f"qty={p.get('qty',0):.6f} @ avg=${(p.get('avg_price') or 0):.6f}"
                        )
                    tg_send(chat_id, "📊 Positions:\n" + "\n".join(lines))
            else:
                tg_send(chat_id, "get_positions() not implemented in engine.")
        except Exception as e:
            logger.exception("positions")
            tg_send(chat_id, f"Positions error: {e}")
        return Response("ok", status=200)

    # NEW: pnl snapshot
    if low.startswith("/pnl"):
        try:
            pnl = getattr(engine, "pnl_usd", 0.0)
            tg_send(chat_id, f"💰 PnL (approx): ${pnl:.2f}")
        except Exception as e:
            logger.exception("pnl")
            tg_send(chat_id, f"PnL error: {e}")
        return Response("ok", status=200)

    if low.startswith("/mode"):
        try:
            parts = low.split()
            if len(parts) == 2 and parts[1] in ("mock", "live"):
                if hasattr(engine, "set_mode"):
                    engine.set_mode(parts[1])
                    tg_send(chat_id, f"Mode set to {parts[1]}")
                else:
                    tg_send(chat_id, "set_mode() not implemented in engine.")
            else:
                tg_send(chat_id, "Usage: /mode mock|live")
        except Exception as e:
            logger.exception("mode")
            tg_send(chat_id, f"Mode error: {e}")
        return Response("ok", status=200)

    if low.startswith("/pause"):
        try:
            if hasattr(engine, "pause"):
                engine.pause()
                tg_send(chat_id, "Engine paused")
            else:
                tg_send(chat_id, "pause() not implemented in engine.")
        except Exception as e:
            logger.exception("pause")
            tg_send(chat_id, f"Pause error: {e}")
        return Response("ok", status=200)

    if low.startswith("/resume"):
        try:
            if hasattr(engine, "resume"):
                engine.resume()
                tg_send(chat_id, "Engine resumed")
            else:
                tg_send(chat_id, "resume() not implemented in engine.")
        except Exception as e:
            logger.exception("resume")
            tg_send(chat_id, f"Resume error: {e}")
        return Response("ok", status=200)

    if low.startswith("/buy"):
        token = text.split(" ", 1)[1].strip() if " " in text else ""
        try:
            if hasattr(engine, "manual_buy"):
                res = engine.manual_buy(token)
                tg_send(chat_id, res or "Buy attempted.")
            else:
                tg_send(chat_id, "manual_buy() not implemented in engine.")
        except Exception as e:
            logger.exception("buy")
            tg_send(chat_id, f"Buy error: {e}")
        return Response("ok", status=200)

    if low.startswith("/sell"):
        token = text.split(" ", 1)[1].strip() if " " in text else ""
        try:
            if hasattr(engine, "manual_sell"):
                res = engine.manual_sell(token)
                tg_send(chat_id, res or "Sell attempted.")
            else:
                tg_send(chat_id, "manual_sell() not implemented in engine.")
        except Exception as e:
            logger.exception("sell")
            tg_send(chat_id, f"Sell error: {e}")
        return Response("ok", status=200)

    if low.startswith("/ping"):
        tg_send(chat_id, "pong")
        return Response("ok", status=200)

    # default help
    tg_send(
        chat_id,
        "Commands: /start /status /positions /pnl /mode mock|live /pause /resume /buy <token> /sell <token> /ping"
    )
    return Response("ok", status=200)

# ===== BOOT =====
def boot():
    try:
        ensure_webhook()
    except Exception as e:
        logger.warning("Webhook not set: %s", e)
    start_jobs()
    # Boot ping proves our token can send
    try:
        if ADMIN_CHAT_ID:
            tg_send(ADMIN_CHAT_ID, "✅ Boot OK (service live)")
    except Exception:
        pass
    if AUTO_START:
        try:
            if hasattr(engine, "resume"):
                engine.resume()
        except Exception as e:
            logger.warning("Auto resume failed: %s", e)

# Run boot at import (works under gunicorn -w 1)
boot()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
