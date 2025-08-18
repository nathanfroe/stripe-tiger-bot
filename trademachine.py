# bot.py — webhook-first Telegram bot with APScheduler, safe watchdog,
# and a full command set wired into TradeMachine.

import os
import json
import time
import logging
from datetime import datetime, timezone as dt_tz

import requests
from flask import Flask, request, Response
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential

# ===================== ENV =====================
TOKEN         = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("ADMIN_CHAT_ID", "")
ALERT_CHAT_ID = os.getenv("ALERT_CHAT_ID", ADMIN_CHAT_ID)
SELF_URL      = os.getenv("SELF_URL", "")  # https://your-app.onrender.com
WEBHOOK_URL   = os.getenv("WEBHOOK_URL", "")  # https://your-app.onrender.com/webhook
TZ_NAME       = os.getenv("TIMEZONE", "UTC")

HEARTBEAT_SEC = int(os.getenv("HEARTBEAT_INTERVAL", "900"))  # 15m
PORT          = int(os.getenv("PORT", "10000"))
AUTO_START    = os.getenv("AUTO_START", "true").lower() == "true"

# Watchdog (keeps talking if webhook is quiet, which you said you like)
WD_CHECK_EVERY  = int(os.getenv("WD_CHECK_EVERY", "120"))   # check every 2m
WD_QUIET_LIMIT  = int(os.getenv("WD_QUIET_LIMIT", "180"))   # if no hits for 3m => do a short polling burst
POLL_BURST_SEC  = int(os.getenv("POLL_BURST_SEC", "15"))    # seconds to burst poll
POLL_INTERVAL_S = int(os.getenv("POLL_INTERVAL_S", "2"))    # frequency of getUpdates during burst

# ===================== LOGGING =====================
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bot")

# ===================== Telegram send helper =====================
def tg_send(chat_id: str, text: str):
    if not TOKEN or not chat_id:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=12,
        )
        if r.status_code != 200:
            logger.error("sendMessage failed: %s | body=%s", r.status_code, r.text)
    except Exception as e:
        logger.exception("Telegram send error: %s", e)

# ===================== ENGINE =====================
from trademachine import (
    TradeMachine,
    _best_dexscreener_pair_usd,
    ETH_TOKEN_ADDRESS,
    BSC_TOKEN_ADDRESS,
)

engine = TradeMachine(tg_sender=tg_send)

try:
    if hasattr(engine, "set_sender"):
        engine.set_sender(tg_send)
        tg_send(ALERT_CHAT_ID, "🔌 Sender re-wired")
except Exception as e:
    logger.warning("Could not attach Telegram sender to engine: %s", e)

# ===================== FLASK =====================
app = Flask(__name__)

# Track last webhook hit so the watchdog can “talk”
_last_webhook_hit_ts = time.time()

# ===================== SCHEDULER =====================
sched = BackgroundScheduler(timezone=TZ_NAME)

def heartbeat():
    ts = datetime.now(dt_tz.utc).isoformat(timespec="seconds")
    tg_send(ALERT_CHAT_ID, f"❤️ heartbeat {ts}")

def trading_cycle():
    try:
        if hasattr(engine, "run_cycle"):
            engine.run_cycle()
        elif hasattr(engine, "run"):
            engine.run()
        else:
            tg_send(ALERT_CHAT_ID, "⚠️ Engine missing run/run_cycle.")
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

def poll_burst(seconds=POLL_BURST_SEC):
    """Temporary getUpdates burst without touching webhook config."""
    end = time.time() + max(5, int(seconds))
    offset = None
    warned = False
    while time.time() < end:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                params={"timeout": 1, **({"offset": offset} if offset else {})},
                timeout=5,
            )
            data = r.json()
            if not warned:
                tg_send(ALERT_CHAT_ID, "🛟 No webhook hits — entering temporary polling burst.")
                warned = True
            if data.get("ok"):
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    # Re-feed the update to our webhook handler path
                    with app.test_request_context("/webhook", method="POST", json=upd):
                        webhook()
        except Exception as e:
            logger.warning("poll burst error: %s", e)
        time.sleep(max(1, POLL_INTERVAL_S))
    tg_send(ALERT_CHAT_ID, "🧩 Webhook restored after polling burst.")

def webhook_watchdog():
    # chatty watchdog that you asked to keep
    quiet_for = time.time() - _last_webhook_hit_ts
    if quiet_for >= WD_QUIET_LIMIT:
        poll_burst(POLL_BURST_SEC)

def start_jobs():
    sched.add_job(heartbeat, "interval", seconds=HEARTBEAT_SEC, id="heartbeat", replace_existing=True)
    poll_secs = getattr(engine, "poll_seconds", 60)
    sched.add_job(trading_cycle, "interval", seconds=poll_secs, id="trading_cycle", replace_existing=True)
    if SELF_URL:
        sched.add_job(keepalive, "interval", seconds=300, id="keepalive", replace_existing=True)
    # watchdog every WD_CHECK_EVERY
    sched.add_job(webhook_watchdog, "interval", seconds=max(30, WD_CHECK_EVERY), id="webhook_watchdog", replace_existing=True)

    if not sched.running:
        sched.start()
    logger.info("Scheduler started.")

# ===================== WEBHOOK MGMT =====================
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
            "allowed_updates": json.dumps(["message", "edited_message"]),
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"setWebhook failed: {r.text}")
    info = _get_wh_info()
    logger.info("Webhook set OK. getWebhookInfo=%s", info)

# ===================== ROUTES =====================
@app.route("/", methods=["GET"])
def root():
    return Response("OK", status=200)

@app.route("/healthz", methods=["GET"])
def healthz():
    return Response("healthy", status=200)

# Self-test endpoint for Render/CLI
@app.route("/__selftest", methods=["POST"])
def __selftest():
    data = request.get_json(silent=True) or {}
    fake = {
        "message": {
            "chat": {"id": data.get("chat_id", ADMIN_CHAT_ID)},
            "text": data.get("text", "/ping"),
        }
    }
    with app.test_request_context("/webhook", method="POST", json=fake):
        return webhook()

# util for /price
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

# ===================== TELEGRAM WEBHOOK =====================
@app.route("/webhook", methods=["POST"])
def webhook():
    global _last_webhook_hit_ts
    _last_webhook_hit_ts = time.time()

    update = request.get_json(silent=True) or {}
    msg = update.get("message") or update.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id") or "") or ADMIN_CHAT_ID

    if not text:
        return Response("no-text", status=200)

    low = text.lower()

    # ------- Commands -------
    if low.startswith("/start") or low.startswith("/help") or low == "/menu":
        tg_send(chat_id,
                "🐯 Stripe Tiger bot is live.\n\n"
                "Commands:\n"
                "/status /price /positions /pnl /cycle /log\n"
                "/buy <addr> /sell <addr>\n"
                "/seteth <addr> /setbsc <addr>\n"
                "/mode mock|live /pause /resume\n"
                "/diag /debugwebhook /forcewebhook /forcepoll /setalert <chat_id>\n"
                "/echo <text> /ping")
        try:
            if hasattr(engine, "status_text"):
                tg_send(chat_id, engine.status_text())
        except Exception:
            pass
        return Response("ok", status=200)

    if low.startswith("/status"):
        try:
            tg_send(chat_id, engine.status_text())
        except Exception as e:
            logger.exception("status")
            tg_send(chat_id, f"Status error: {e}")
        return Response("ok", status=200)

    if low.startswith("/mode"):
        parts = low.split()
        try:
            if len(parts) == 2 and parts[1] in ("mock", "live"):
                if hasattr(engine, "set_mode"):
                    engine.set_mode(parts[1])
                    tg_send(chat_id, f"Mode set to {parts[1]}")
                else:
                    tg_send(chat_id, "set_mode() not implemented.")
            else:
                tg_send(chat_id, "Usage: /mode mock|live")
        except Exception as e:
            logger.exception("mode")
            tg_send(chat_id, f"Mode error: {e}")
        return Response("ok", status=200)

    if low.startswith("/pause"):
        try:
            engine.pause()
            tg_send(chat_id, "Engine paused")
        except Exception as e:
            logger.exception("pause")
            tg_send(chat_id, f"Pause error: {e}")
        return Response("ok", status=200)

    if low.startswith("/resume"):
        try:
            engine.resume()
            tg_send(chat_id, "Engine resumed")
        except Exception as e:
            logger.exception("resume")
            tg_send(chat_id, f"Resume error: {e}")
        return Response("ok", status=200)

    if low.startswith("/buy"):
        token = text.split(" ", 1)[1].strip() if " " in text else ""
        try:
            res = engine.manual_buy(token or "")
            tg_send(chat_id, res or "Buy attempted.")
        except Exception as e:
            logger.exception("buy")
            tg_send(chat_id, f"Buy error: {e}")
        return Response("ok", status=200)

    if low.startswith("/sell"):
        token = text.split(" ", 1)[1].strip() if " " in text else ""
        try:
            res = engine.manual_sell(token or "")
            tg_send(chat_id, res or "Sell attempted.")
        except Exception as e:
            logger.exception("sell")
            tg_send(chat_id, f"Sell error: {e}")
        return Response("ok", status=200)

    if low.startswith("/seteth"):
        addr = text.split(" ", 1)[1].strip() if " " in text else ""
        res = engine.set_eth_token(addr)
        tg_send(chat_id, res)
        return Response("ok", status=200)

    if low.startswith("/setbsc"):
        addr = text.split(" ", 1)[1].strip() if " " in text else ""
        res = engine.set_bsc_token(addr)
        tg_send(chat_id, res)
        return Response("ok", status=200)

    if low.startswith("/price"):
        lines = ["📈 Prices (Dexscreener):"]
        lines.append(_fmt_price_line("ETH", engine.eth_token or ETH_TOKEN_ADDRESS))
        lines.append(_fmt_price_line("BSC", engine.bsc_token or BSC_TOKEN_ADDRESS))
        tg_send(chat_id, "\n".join(lines))
        return Response("ok", status=200)

    if low.startswith("/positions"):
        try:
            pos = engine.get_positions()
            if not pos:
                tg_send(chat_id, "No positions.")
            else:
                lines = ["📦 Positions:"]
                for p in pos:
                    lines.append(
                        f"{p['chain']} | {p['token']} qty={p['qty']:.6f} "
                        f"avg=${p['avg_price']:.6f} val≈${p['market_value']:.2f}"
                    )
                tg_send(chat_id, "\n".join(lines))
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
            engine.run_cycle()
            tg_send(chat_id, "🔁 Ran one cycle.")
        except Exception as e:
            logger.exception("cycle-now")
            tg_send(chat_id, f"Cycle error: {e}")
        return Response("ok", status=200)

    if low.startswith("/log"):
        try:
            tg_send(chat_id, engine.recent_events_text(12))
        except Exception as e:
            tg_send(chat_id, f"Log error: {e}")
        return Response("ok", status=200)

    if low.startswith("/diag"):
        tg_send(chat_id, json.dumps(_get_wh_info(), indent=2))
        return Response("ok", status=200)

    if low.startswith("/forcewebhook"):
        try:
            ensure_webhook()
            tg_send(chat_id, "Webhook forced/set.")
        except Exception as e:
            tg_send(chat_id, f"forcewebhook error: {e}")
        return Response("ok", status=200)

    if low.startswith("/debugwebhook"):
        tg_send(chat_id, f"Last webhook hit {int(time.time()-_last_webhook_hit_ts)}s ago")
        return Response("ok", status=200)

    if low.startswith("/forcepoll"):
        poll_burst(POLL_BURST_SEC)
        return Response("ok", status=200)

    if low.startswith("/setalert"):
        arg = text.split(" ", 1)[1].strip() if " " in text else ""
        if arg:
            os.environ["ALERT_CHAT_ID"] = arg
            tg_send(chat_id, f"ALERT_CHAT_ID set to {arg}")
        else:
            tg_send(chat_id, "Usage: /setalert <chat_id>")
        return Response("ok", status=200)

    if low.startswith("/echo"):
        tg_send(chat_id, text.split(" ", 1)[1] if " " in text else "(empty)")
        return Response("ok", status=200)

    if low.startswith("/ping"):
        tg_send(chat_id, "pong")
        return Response("ok", status=200)

    # default help
    tg_send(
        chat_id,
        "Commands:\n"
        "/status /price /positions /pnl /cycle /log\n"
        "/buy <addr> /sell <addr> /seteth <addr> /setbsc <addr>\n"
        "/mode mock|live /pause /resume /diag /debugwebhook /forcewebhook /forcepoll\n"
        "/setalert <chat_id> /echo <text> /ping"
    )
    return Response("ok", status=200)

# ===================== BOOT =====================
def boot():
    try:
        ensure_webhook()
    except Exception as e:
        logger.warning("Webhook not set: %s", e)
    start_jobs()

    try:
        if ADMIN_CHAT_ID:
            tg_send(ADMIN_CHAT_ID, "✅ Boot OK (service live)")
            tg_send(ADMIN_CHAT_ID, engine.status_text())
    except Exception:
        pass

    if AUTO_START:
        try:
            engine.resume()
        except Exception as e:
            logger.warning("Auto resume failed: %s", e)

boot()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
