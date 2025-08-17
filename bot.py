# bot.py — webhook + APScheduler + keepalive + rich commands + event log

import os
import json
import logging
from collections import deque
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
from trademachine import (
    TradeMachine,
    _best_dexscreener_pair_usd,
    ETH_TOKEN_ADDRESS,
    BSC_TOKEN_ADDRESS,
)

# simple in-memory event log (engine will write here via callback)
EVENTS = deque(maxlen=300)

def _log_event(kind: str, **kw):
    ts = datetime.now(dt_tz.utc).isoformat(timespec="seconds")
    EVENTS.append((ts, kind, kw))
    # also mirror critical items to alert chat
    if kind in ("error", "warn"):
        try:
            tg_send(ALERT_CHAT_ID, f"[{kind.upper()}] {kw}")
        except Exception:
            pass

engine = TradeMachine(tg_sender=tg_send)
# wire the event logger if engine exposes the hook
try:
    setattr(engine, "log_event_cb", _log_event)
except Exception:
    pass

# Optional rewire confirmation
try:
    if hasattr(engine, "set_sender"):
        engine.set_sender(tg_send)
        tg_send(ALERT_CHAT_ID, "🔌 Sender re-wired")
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

# Self-test endpoint for Render (simulate a Telegram POST)
@app.route("/__selftest", methods=["POST"])
def __selftest():
    data = request.get_json(silent=True) or {}
    fake = {
        "message": {
            "chat": {"id": data.get("chat_id", ADMIN_CHAT_ID)},
            "text": data.get("text", "/ping")
        }
    }
    with app.test_request_context("/webhook", method="POST", json=fake):
        return webhook()

# ===== HELPERS =====
def _fmt_price_line(chain: str, token_addr: str) -> str:
    if not token_addr:
        return f"{chain}: (no token configured)"
    try:
        price, liq = _best_dexscreener_pair_usd(token_addr, chain)
        if price is None or liq is None:
            return f"{chain}: {token_addr[:6]}...{token_addr[-4:]} → No price/liquidity"
        return f"{chain}: {token_addr[:6]}...{token_addr[-4:]} → ${price:.6f} | liq≈${liq:,.0f}"
    except Exception as e:
        logger.exception("price fetch error")
        return f"{chain}: error: {e}"

def _events_tail(n: int = 20) -> str:
    items = list(EVENTS)[-n:]
    if not items:
        return "(no recent events)"
    lines = []
    for ts, kind, kw in items:
        short = json.dumps(kw, separators=(",", ":"), ensure_ascii=False)
        if len(short) > 160:
            short = short[:157] + "…"
        lines.append(f"{ts} | {kind}: {short}")
    return "\n".join(lines)

# ===== TELEGRAM WEBHOOK =====
@app.route("/webhook", methods=["POST"])
def webhook():
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

    # Commands
    if low.startswith("/start"):
        tg_send(chat_id, "🐯 Stripe Tiger bot is live.")
        try:
            if hasattr(engine, "status_text"):
                tg_send(chat_id, engine.status_text())
        except Exception:
            pass
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
                res = engine.manual_buy(token or ETH_TOKEN_ADDRESS or BSC_TOKEN_ADDRESS or "")
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
                res = engine.manual_sell(token or ETH_TOKEN_ADDRESS or BSC_TOKEN_ADDRESS or "")
                tg_send(chat_id, res or "Sell attempted.")
            else:
                tg_send(chat_id, "manual_sell() not implemented in engine.")
        except Exception as e:
            logger.exception("sell")
            tg_send(chat_id, f"Sell error: {e}")
        return Response("ok", status=200)

    if low.startswith("/price"):
        lines = ["📈 Prices:"]
        lines.append(_fmt_price_line("ETH", ETH_TOKEN_ADDRESS))
        lines.append(_fmt_price_line("BSC", BSC_TOKEN_ADDRESS))
        tg_send(chat_id, "\n".join(lines))
        return Response("ok", status=200)

    if low.startswith("/positions"):
        try:
            out = []
            if hasattr(engine, "get_positions"):
                for p in engine.get_positions():
                    out.append(f"{p['chain']} {p['token'][:6]}...{p['token'][-4:]} qty={p['qty']:.6f} avg={p['avg_price']}")
            tg_send(chat_id, "📦 Positions:\n" + ("\n".join(out) if out else "(none)"))
        except Exception as e:
            logger.exception("positions")
            tg_send(chat_id, f"Positions error: {e}")
        return Response("ok", status=200)

    if low.startswith("/pnl"):
        try:
            pnl = getattr(engine, "pnl_usd", 0.0)
            count = len(getattr(engine, "positions", {}) or {})
            tg_send(chat_id, f"💰 PnL≈${pnl:.2f} | positions={count}")
        except Exception as e:
            logger.exception("pnl")
            tg_send(chat_id, f"PnL error: {e}")
        return Response("ok", status=200)

    if low.startswith("/cycle") or low.startswith("/think"):
        try:
            if hasattr(engine, "run_cycle"):
                engine.run_cycle()
                tg_send(chat_id, "🔁 Ran one cycle.")
            else:
                tg_send(chat_id, "run_cycle() not implemented in engine.")
        except Exception as e:
            logger.exception("cycle-now")
            tg_send(chat_id, f"Cycle error: {e}")
        return Response("ok", status=200)

    # NEW: show recent internal events the engine recorded
    if low.startswith("/log"):
        try:
            n = 30
            parts = text.split()
            if len(parts) == 2 and parts[1].isdigit():
                n = max(1, min(120, int(parts[1])))
            tg_send(chat_id, "🧾 Recent events:\n" + _events_tail(n))
        except Exception as e:
            logger.exception("log")
            tg_send(chat_id, f"Log error: {e}")
        return Response("ok", status=200)

    # NEW: webhook info (debug why commands aren’t reaching you)
    if low.startswith("/wh"):
        try:
            info = _get_wh_info()
            # short summary
            last_err = info.get("last_error_message") or "none"
            pending = info.get("pending_update_count")
            url = info.get("url")
            tg_send(chat_id, f"🔎 Webhook\n• url: {url}\n• pending: {pending}\n• last_error: {last_err}")
        except Exception as e:
            logger.exception("wh")
            tg_send(chat_id, f"Webhook info error: {e}")
        return Response("ok", status=200)

    if low.startswith("/ping"):
        tg_send(chat_id, "pong")
        return Response("ok", status=200)

    tg_send(
        chat_id,
        "Commands:\n"
        "/start /status /mode mock|live /pause /resume\n"
        "/price /positions /pnl /cycle /log [n] /wh\n"
        "/buy <token> /sell <token> /ping"
    )
    return Response("ok", status=200)

# ===== BOOT =====
def boot():
    try:
        ensure_webhook()
    except Exception as e:
        logger.warning("Webhook not set: %s", e)
    start_jobs()
    try:
        if ADMIN_CHAT_ID:
            tg_send(ADMIN_CHAT_ID, "✅ Boot OK (service live)")
            if hasattr(engine, "status_text"):
                tg_send(ADMIN_CHAT_ID, engine.status_text())
    except Exception:
        pass
    if AUTO_START:
        try:
            if hasattr(engine, "resume"):
                engine.resume()
        except Exception as e:
            logger.warning("Auto resume failed: %s", e)

boot()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
