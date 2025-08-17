# bot.py — robust webhook handler + APScheduler + keepalive + full commands

import os, json, logging
from collections import deque
from datetime import datetime, timezone as dt_tz

import requests
from flask import Flask, request, Response
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential

# ===== ENV =====
TOKEN         = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("ADMIN_CHAT_ID", "")
ALERT_CHAT_ID = os.getenv("ALERT_CHAT_ID", ADMIN_CHAT_ID)
WEBHOOK_URL   = os.getenv("WEBHOOK_URL", "")   # https://<your-app>/webhook
HEARTBEAT_SEC = int(os.getenv("HEARTBEAT_INTERVAL", "900"))
AUTO_START    = os.getenv("AUTO_START", "true").lower() == "true"
PORT          = int(os.getenv("PORT", "10000"))
SELF_URL      = os.getenv("SELF_URL", "")
TZ_NAME       = os.getenv("TIMEZONE", "UTC")

# ===== LOGGING =====
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("bot")

# ===== Telegram helper =====
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
            logger.error("sendMessage failed: %s | %s", r.status_code, r.text)
    except Exception as e:
        logger.exception("Telegram send error: %s", e)

# ===== ENGINE =====
import trademachine as tm
from trademachine import TradeMachine, _best_dexscreener_pair_usd

engine = TradeMachine(tg_sender=tg_send)

# In-memory circular log (also fed by the engine)
EVENTS = deque(maxlen=50)
def log_event(kind: str, **kw):
    ts = datetime.now(dt_tz.utc).strftime("%H:%M:%S")
    try:
        msg = f"{ts} | {kind} | " + " ".join(f"{k}={kw[k]}" for k in sorted(kw))
    except Exception:
        msg = f"{ts} | {kind} | {kw}"
    EVENTS.append(msg)
# wire it into the engine so fills/signals/errors show up in /log
try:
    engine.log_event_cb = log_event
except Exception:
    pass

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
        EVENTS.append(f"{datetime.now(dt_tz.utc).strftime('%H:%M:%S')} | error | {e}")

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
    sched.add_job(trading_cycle, "interval", seconds=getattr(engine, "poll_seconds", 60),
                  id="trading_cycle", replace_existing=True)
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
            "allowed_updates": json.dumps(["message", "edited_message", "channel_post"])
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"setWebhook failed: {r.text}")
    logger.info("Webhook set OK. getWebhookInfo=%s", _get_wh_info())

# ===== ROUTES =====
@app.route("/", methods=["GET"])
def root():
    return Response("OK", status=200)

@app.route("/healthz", methods=["GET"])
def healthz():
    return Response("healthy", status=200)

@app.route("/__selftest", methods=["POST"])
def __selftest():
    data = request.get_json(silent=True) or {}
    fake = {
        "message": {"chat": {"id": data.get("chat_id", ADMIN_CHAT_ID)}, "text": data.get("text", "/ping")}
    }
    with app.test_request_context("/webhook", method="POST", json=fake):
        return webhook()

# ===== HELPERS =====
def _parse_update() -> tuple[str, str]:
    """Return (chat_id, text) from message/edited_message/channel_post; '' if missing."""
    # log headers + raw body for troubleshooting
    try:
        logger.info("Webhook headers: %s", dict(request.headers))
    except Exception:
        pass
    raw = request.get_data(as_text=True) or ""
    if raw:
        logger.info("Webhook raw len=%d", len(raw))

    update = request.get_json(silent=True)
    if update is None and raw:
        try:
            update = json.loads(raw)
        except Exception:
            update = {}

    msg = update.get("message") or update.get("edited_message") or update.get("channel_post") or {}
    chat = (msg.get("chat") or {})
    chat_id = str(chat.get("id") or "") or ADMIN_CHAT_ID
    text = (msg.get("text") or "").strip()
    return chat_id, text

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

def _set_token(chain: str, addr: str) -> str:
    if not (addr and addr.startswith("0x") and len(addr) == 42):
        return "⚠️ Invalid address. Must be a 42-char 0x… string."
    if chain == "ETH":
        tm.ETH_TOKEN_ADDRESS = addr
    else:
        tm.BSC_TOKEN_ADDRESS = addr
    return f"✅ {chain} token set to {addr}"

# ===== TELEGRAM WEBHOOK =====
@app.route("/webhook", methods=["POST"])
def webhook():
    chat_id, text = _parse_update()
    if not text:
        return Response("no-text", status=200)

    low = text.lower()
    logger.info("Update: chat=%s | text=%r", chat_id, text)

    # --- Commands ---
    if low.startswith("/start"):
        tg_send(chat_id, "🐯 Stripe Tiger bot is live.")
        if hasattr(engine, "status_text"):
            tg_send(chat_id, engine.status_text())
        return Response("ok", status=200)

    if low.startswith("/status"):
        tg_send(chat_id, engine.status_text() if hasattr(engine, "status_text") else "No status_text().")
        return Response("ok", status=200)

    if low.startswith("/mode"):
        parts = low.split()
        if len(parts) == 2 and parts[1] in ("mock", "live"):
            if hasattr(engine, "set_mode"):
                engine.set_mode(parts[1])
                tg_send(chat_id, f"Mode set to {parts[1]}")
        else:
            tg_send(chat_id, "Usage: /mode mock|live")
        return Response("ok", status=200)

    if low.startswith("/pause"):
        if hasattr(engine, "pause"): engine.pause()
        tg_send(chat_id, "Engine paused")
        return Response("ok", status=200)

    if low.startswith("/resume"):
        if hasattr(engine, "resume"): engine.resume()
        tg_send(chat_id, "Engine resumed")
        return Response("ok", status=200)

    if low.startswith("/seteth"):
        addr = text.split(" ", 1)[1].strip() if " " in text else ""
        tg_send(chat_id, _set_token("ETH", addr))
        return Response("ok", status=200)

    if low.startswith("/setbsc"):
        addr = text.split(" ", 1)[1].strip() if " " in text else ""
        tg_send(chat_id, _set_token("BSC", addr))
        return Response("ok", status=200)

    if low.startswith("/price"):
        lines = ["📈 Prices (Dexscreener):",
                 _fmt_price_line("ETH", tm.ETH_TOKEN_ADDRESS),
                 _fmt_price_line("BSC", tm.BSC_TOKEN_ADDRESS)]
        tg_send(chat_id, "\n".join(lines))
        return Response("ok", status=200)

    if low.startswith("/positions"):
        pos = []
        if hasattr(engine, "get_positions"):
            for p in engine.get_positions():
                pos.append(f"{p['chain']} {p['token']} qty={p['qty']:.6f} avg={p['avg_price']}")
        tg_send(chat_id, "📦 Positions:\n" + ("\n".join(pos) if pos else "None"))
        return Response("ok", status=200)

    if low.startswith("/pnl"):
        pnl = getattr(engine, "pnl_usd", 0.0)
        cnt = len(getattr(engine, "positions", {}) or {})
        tg_send(chat_id, f"💰 PnL≈${pnl:.2f} | positions={cnt}")
        return Response("ok", status=200)

    if low.startswith("/cycle") or low.startswith("/think"):
        try:
            engine.run_cycle()
            tg_send(chat_id, "🔁 Ran one cycle.")
        except Exception as e:
            tg_send(chat_id, f"Cycle error: {e}")
        return Response("ok", status=200)

    if low.startswith("/buy"):
        token = text.split(" ", 1)[1].strip() if " " in text else (tm.ETH_TOKEN_ADDRESS or tm.BSC_TOKEN_ADDRESS or "")
        res = engine.manual_buy(token) if hasattr(engine, "manual_buy") else "manual_buy() missing"
        tg_send(chat_id, res)
        return Response("ok", status=200)

    if low.startswith("/sell"):
        token = text.split(" ", 1)[1].strip() if " " in text else (tm.ETH_TOKEN_ADDRESS or tm.BSC_TOKEN_ADDRESS or "")
        res = engine.manual_sell(token) if hasattr(engine, "manual_sell") else "manual_sell() missing"
        tg_send(chat_id, res)
        return Response("ok", status=200)

    if low.startswith("/log"):
        if not EVENTS:
            tg_send(chat_id, "🧾 No recent events.")
        else:
            tg_send(chat_id, "🧾 Recent events:\n" + "\n".join(EVENTS))
        return Response("ok", status=200)

    if low.startswith("/ping"):
        tg_send(chat_id, "pong")
        return Response("ok", status=200)

    # default help
    tg_send(
        chat_id,
        "Commands:\n"
        "/start /status /mode mock|live /pause /resume\n"
        "/seteth <addr> /setbsc <addr>\n"
        "/price /positions /pnl /cycle\n"
        "/buy <addr?> /sell <addr?> /log /ping"
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
            engine.resume()
        except Exception as e:
            logger.warning("Auto resume failed: %s", e)

boot()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
