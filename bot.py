# bot.py — webhook + APScheduler + keepalive + rich Telegram commands (no parse_mode)

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
WEBHOOK_URL      = os.getenv("WEBHOOK_URL", "")
HEARTBEAT_SEC    = int(os.getenv("HEARTBEAT_INTERVAL", "900"))
AUTO_START       = os.getenv("AUTO_START", "true").lower() == "true"
PORT             = int(os.getenv("PORT", "10000"))
SELF_URL         = os.getenv("SELF_URL", "")
TZ_NAME          = os.getenv("TIMEZONE", "UTC")

# ===== LOGGING =====
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("bot")

# ===== Telegram send helper (plain text; we do NOT set parse_mode) =====
def tg_send(chat_id: str, text: str):
    if not TOKEN or not chat_id:
        return
    try:
        # Telegram has a 4096 char limit; trim just in case
        if text and len(text) > 4000:
            text = text[:3990] + "\n…[truncated]"
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=12,
        )
        if r.status_code != 200:
            logger.error("sendMessage failed: %s | body=%s", r.status_code, r.text)
    except Exception as e:
        logger.exception("Telegram send error: %s", e)

# ===== ENGINE =====
from trademachine import (
    TradeMachine,
    _best_dexscreener_pair_usd,  # reuse helper for /price
)

engine = TradeMachine(tg_sender=tg_send)

# Optional compatibility setter
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

# Self-test endpoint for Render
@app.route("/__selftest", methods=["POST"])
def __selftest():
    data = request.get_json(silent=True) or {}
    fake = {"message": {"chat": {"id": data.get("chat_id", ADMIN_CHAT_ID)}, "text": data.get("text", "/ping")}}
    with app.test_request_context("/webhook", method="POST", json=fake):
        return webhook()

# ===== UTIL =====
def _fmt_price_line(chain: str, token_addr: str) -> str:
    if not token_addr:
        return f"{chain}: (no token configured)"
    try:
        price, liq = _best_dexscreener_pair_usd(token_addr, chain)
        if price is None or liq is None:
            return f"{chain}: {token_addr} → No price/liquidity"
        return f"{chain}: {token_addr} → ${price:.6f} | liq≈${liq:,.0f}"
    except Exception as e:
        logger.exception("price fetch error")
        return f"{chain}: error: {e}"

def _events_text(limit: int = 15) -> str:
    ev = getattr(engine, "events", [])[-limit:]
    if not ev:
        return "No recent events."
    lines = ["🧾 Recent events:"]
    for e in ev:
        ts = e.get("ts") or ""
        kind = e.get("kind") or ""
        rest = {k: v for k, v in e.items() if k not in ("ts", "kind")}
        lines.append(f"{ts} | {kind} | {rest}")
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

    # ------- Commands -------
    if low.startswith(("/start", "/help", "/menu")):
        tg_send(chat_id,
            "🐯 Stripe Tiger bot is live.\n\n"
            "Commands:\n"
            "/status – current config\n"
            "/price – live price + liquidity\n"
            "/positions – open positions\n"
            "/pnl – running PnL\n"
            "/cycle – run one immediate tick\n"
            "/buy <addr> – mock/live buy\n"
            "/sell <addr> – mock/live sell\n"
            "/seteth <addr> – set tracked ETH token\n"
            "/setbsc <addr> – set tracked BSC token\n"
            "/mode mock|live – switch mode\n"
            "/pause /resume – control engine\n"
            "/log – last events"
        )
        try:
            if hasattr(engine, "status_text"):
                tg_send(chat_id, engine.status_text())
        except Exception:
            pass
        return Response("ok", status=200)

    if low.startswith("/status"):
        try:
            tg_send(chat_id, engine.status_text() if hasattr(engine, "status_text") else "status_text() missing")
        except Exception as e:
            logger.exception("status")
            tg_send(chat_id, f"Status error: {e}")
        return Response("ok", status=200)

    if low.startswith("/mode"):
        try:
            parts = low.split()
            if len(parts) == 2 and parts[1] in ("mock", "live"):
                engine.set_mode(parts[1]) if hasattr(engine, "set_mode") else None
                tg_send(chat_id, f"Mode set to {parts[1]}")
            else:
                tg_send(chat_id, "Usage: /mode mock|live")
        except Exception as e:
            logger.exception("mode")
            tg_send(chat_id, f"Mode error: {e}")
        return Response("ok", status=200)

    if low.startswith("/pause"):
        try:
            engine.pause() if hasattr(engine, "pause") else None
            tg_send(chat_id, "Engine paused")
        except Exception as e:
            logger.exception("pause")
            tg_send(chat_id, f"Pause error: {e}")
        return Response("ok", status=200)

    if low.startswith("/resume"):
        try:
            engine.resume() if hasattr(engine, "resume") else None
            tg_send(chat_id, "Engine resumed")
        except Exception as e:
            logger.exception("resume")
            tg_send(chat_id, f"Resume error: {e}")
        return Response("ok", status=200)

    if low.startswith("/seteth"):
        addr = text.split(" ", 1)[1].strip() if " " in text else ""
        try:
            engine.set_token("ETH", addr)
            tg_send(chat_id, f"ETH token set to {addr}")
        except Exception as e:
            logger.exception("seteth")
            tg_send(chat_id, f"seteth error: {e}")
        return Response("ok", status=200)

    if low.startswith("/setbsc"):
        addr = text.split(" ", 1)[1].strip() if " " in text else ""
        try:
            engine.set_token("BSC", addr)
            tg_send(chat_id, f"BSC token set to {addr}")
        except Exception as e:
            logger.exception("setbsc")
            tg_send(chat_id, f"setbsc error: {e}")
        return Response("ok", status=200)

    if low.startswith("/buy"):
        token = text.split(" ", 1)[1].strip() if " " in text else ""
        try:
            res = engine.manual_buy(token) if hasattr(engine, "manual_buy") else "manual_buy() missing"
            tg_send(chat_id, res or "Buy attempted.")
        except Exception as e:
            logger.exception("buy")
            tg_send(chat_id, f"Buy error: {e}")
        return Response("ok", status=200)

    if low.startswith("/sell"):
        token = text.split(" ", 1)[1].strip() if " " in text else ""
        try:
            res = engine.manual_sell(token) if hasattr(engine, "manual_sell") else "manual_sell() missing"
            tg_send(chat_id, res or "Sell attempted.")
        except Exception as e:
            logger.exception("sell")
            tg_send(chat_id, f"Sell error: {e}")
        return Response("ok", status=200)

    if low.startswith("/price"):
        lines = ["📈 Prices (Dexscreener):"]
        lines.append(_fmt_price_line("ETH", getattr(engine, "eth_token", None)))
        lines.append(_fmt_price_line("BSC", getattr(engine, "bsc_token", None)))
        tg_send(chat_id, "\n".join(lines))
        return Response("ok", status=200)

    if low.startswith("/positions"):
        try:
            pos = []
            if hasattr(engine, "get_positions"):
                for p in engine.get_positions():
                    pos.append(f"{p['chain']} {p['token']} qty={p['qty']:.6f} avg={p['avg_price']}")
            tg_send(chat_id, "No positions." if not pos else "📦 Positions:\n" + "\n".join(pos))
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
            engine.run_cycle() if hasattr(engine, "run_cycle") else None
            tg_send(chat_id, "🔁 Ran one cycle.")
        except Exception as e:
            logger.exception("cycle-now")
            tg_send(chat_id, f"Cycle error: {e}")
        return Response("ok", status=200)

    if low.startswith("/log"):
        try:
            tg_send(chat_id, _events_text(20))
        except Exception as e:
            logger.exception("log")
            tg_send(chat_id, f"log error: {e}")
        return Response("ok", status=200)

    if low.startswith("/ping"):
        tg_send(chat_id, "pong")
        return Response("ok", status=200)

    tg_send(
        chat_id,
        "Commands:\n"
        "/start /status /mode mock|live /pause /resume\n"
        "/seteth <addr> /setbsc <addr>\n"
        "/price /positions /pnl /cycle /log\n"
        "/buy <addr> /sell <addr> /ping"
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

# Run boot at import (works under gunicorn -w 1)
boot()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
