# bot.py — instrumented webhook, safe sending, manual/auto polling fallback, rich commands

import os, json, logging, time
from datetime import datetime, timezone as dt_tz

import requests
from flask import Flask, request, Response
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential

# ========= ENV =========
TOKEN         = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("ADMIN_CHAT_ID", "")
ALERT_CHAT_ID = os.getenv("ALERT_CHAT_ID", ADMIN_CHAT_ID)
WEBHOOK_URL   = os.getenv("WEBHOOK_URL", "")
SELF_URL      = os.getenv("SELF_URL", "")
PORT          = int(os.getenv("PORT", "10000"))
TZ_NAME       = os.getenv("TIMEZONE", "UTC")
HEARTBEAT_SEC = int(os.getenv("HEARTBEAT_INTERVAL", "900"))
AUTO_START    = os.getenv("AUTO_START", "true").lower() == "true"

# Fallback & diagnostics
WATCHDOG_GRACE_SEC = int(os.getenv("WEBHOOK_WATCHDOG_SEC", "300"))  # 5 min
POLL_BURST_SECONDS = int(os.getenv("POLL_BURST_SECONDS", "45"))     # 45s
POLL_INTERVAL_SEC  = int(os.getenv("POLL_INTERVAL_SEC", "2"))       # 2s

# ========= LOGGING =========
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("bot")

def now_utc(): return datetime.now(dt_tz.utc)

# ========= Telegram helpers =========
def _api(method: str, **params):
    r = requests.get(f"https://api.telegram.org/bot{TOKEN}/{method}", params=params, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"{method} failed: {r.status_code} | {r.text}")
    return r.json()

def tg_send(chat_id: str, text: str):
    """Always attempt; if Telegram rejects, echo error back to ADMIN/ALERT."""
    if not TOKEN or not chat_id: return
    try:
        if text and len(text) > 3900:
            text = text[:3900] + "\n…[truncated]"
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": str(chat_id), "text": text},
            timeout=20,
        )
        if r.status_code != 200:
            err = f"sendMessage failed: {r.status_code} | {r.text}"
            logger.error(err)
            if ALERT_CHAT_ID:
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                        json={"chat_id": str(ALERT_CHAT_ID), "text": f"⚠️ {err}"},
                        timeout=15,
                    )
                except Exception:
                    logger.exception("Secondary alert send failed")
    except Exception:
        logger.exception("tg_send exception")

def get_webhook_info():
    try:
        return _api("getWebhookInfo")
    except Exception as e:
        logger.warning("getWebhookInfo error: %s", e); return {}

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=30))
def set_webhook():
    if not TOKEN or not WEBHOOK_URL:
        logger.warning("TOKEN/WEBHOOK_URL missing; skipping setWebhook"); return
    r = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/setWebhook",
        params={
            "url": WEBHOOK_URL,
            "drop_pending_updates": True,
            "allowed_updates": json.dumps(["message", "edited_message"]),
        },
        timeout=20,
    )
    if r.status_code != 200:
        raise RuntimeError(f"setWebhook failed: {r.status_code} | {r.text}")
    logger.info("Webhook set OK: %s", get_webhook_info())

def delete_webhook():
    try:
        _api("deleteWebhook", drop_pending_updates=False)
        logger.info("Webhook deleted")
    except Exception as e:
        logger.warning("deleteWebhook error: %s", e)

# ========= ENGINE (mock-compatible) =========
from trademachine import TradeMachine, _best_dexscreener_pair_usd
engine = TradeMachine(tg_sender=tg_send)

# ========= STATE =========
BOOT_AT = now_utc()
LAST_WEBHOOK_AT = None
LAST_UPDATE_ID = None
IS_IN_POLL_BURST = False

# ========= FLASK =========
app = Flask(__name__)

@app.route("/", methods=["GET"])
def root(): return Response("OK", status=200)

@app.route("/healthz", methods=["GET"])
def healthz(): return Response("healthy", status=200)

@app.route("/__selftest", methods=["POST"])
def __selftest():
    data = request.get_json(silent=True) or {}
    fake = {"message": {"chat": {"id": data.get("chat_id", ADMIN_CHAT_ID)}, "text": data.get("text", "/ping")}}
    with app.test_request_context("/webhook", method="POST", json=fake):
        return webhook()

# ========= UTIL =========
def fmt_price_line(chain: str, addr: str | None) -> str:
    if not addr: return f"{chain}: (no token configured)"
    try:
        p, liq = _best_dexscreener_pair_usd(addr, chain)
        if p is None or liq is None: return f"{chain}: {addr} → No price/liquidity"
        return f"{chain}: {addr} → ${p:.6f} | liq≈${liq:,.0f}"
    except Exception as e:
        return f"{chain}: error: {e}"

def events_text(n=20) -> str:
    ev = getattr(engine, "events", [])[-n:]
    if not ev: return "No recent events."
    lines = ["🧾 Recent events:"]
    for e in ev:
        ts, kind = e.get("ts", ""), e.get("kind", "")
        rest = {k: v for k, v in e.items() if k not in ("ts", "kind")}
        lines.append(f"{ts} | {kind} | {rest}")
    return "\n".join(lines)

# ========= COMMANDS =========
def handle_command(chat_id: str, text: str):
    low = text.lower()

    if low.startswith(("/start", "/help", "/menu")):
        tg_send(chat_id,
                "🐯 Stripe Tiger bot is live.\n\n"
                "Commands:\n"
                "/status /price /positions /pnl /cycle /log\n"
                "/buy <addr> /sell <addr>\n"
                "/seteth <addr> /setbsc <addr>\n"
                "/mode mock|live /pause /resume\n"
                "/diag /debugwebhook /forcewebhook /forcepoll /setalert <chat_id>\n"
                "/echo <text> /ping"
        )
        try:
            if hasattr(engine, "status_text"):
                tg_send(chat_id, engine.status_text())
        except Exception:
            pass
        return

    if low.startswith("/status"):
        try:
            tg_send(chat_id, engine.status_text() if hasattr(engine, "status_text") else "status_text() missing")
        except Exception as e:
            tg_send(chat_id, f"status error: {e}")
        return

    if low.startswith("/mode"):
        parts = low.split()
        if len(parts) == 2 and parts[1] in ("mock", "live"):
            if hasattr(engine, "set_mode"): engine.set_mode(parts[1])
            tg_send(chat_id, f"Mode set to {parts[1]}")
        else:
            tg_send(chat_id, "Usage: /mode mock|live")
        return

    if low.startswith("/pause"):
        if hasattr(engine, "pause"): engine.pause()
        tg_send(chat_id, "Engine paused"); return

    if low.startswith("/resume"):
        if hasattr(engine, "resume"): engine.resume()
        tg_send(chat_id, "Engine resumed"); return

    if low.startswith("/seteth"):
        addr = text.split(" ", 1)[1].strip() if " " in text else ""
        if hasattr(engine, "set_token"): engine.set_token("ETH", addr)
        tg_send(chat_id, f"ETH token set to {addr}"); return

    if low.startswith("/setbsc"):
        addr = text.split(" ", 1)[1].strip() if " " in text else ""
        if hasattr(engine, "set_token"): engine.set_token("BSC", addr)
        tg_send(chat_id, f"BSC token set to {addr}"); return

    if low.startswith("/buy"):
        addr = text.split(" ", 1)[1].strip() if " " in text else ""
        res = engine.manual_buy(addr) if hasattr(engine, "manual_buy") else "manual_buy() missing"
        tg_send(chat_id, res); return

    if low.startswith("/sell"):
        addr = text.split(" ", 1)[1].strip() if " " in text else ""
        res = engine.manual_sell(addr) if hasattr(engine, "manual_sell") else "manual_sell() missing"
        tg_send(chat_id, res); return

    if low.startswith("/price"):
        tg_send(chat_id, "📈 Prices:\n" +
               "\n".join([
                   fmt_price_line("ETH", getattr(engine, "eth_token", None)),
                   fmt_price_line("BSC", getattr(engine, "bsc_token", None))
               ])); return

    if low.startswith("/positions"):
        pos = []
        if hasattr(engine, "get_positions"):
            for p in engine.get_positions():
                pos.append(f"{p['chain']} {p['token']} qty={p['qty']:.6f} avg={p['avg_price']}")
        tg_send(chat_id, "No positions." if not pos else "📦 Positions:\n" + "\n".join(pos)); return

    if low.startswith("/pnl"):
        pnl = getattr(engine, "pnl_usd", 0.0); cnt = len(getattr(engine, "positions", {}) or {})
        tg_send(chat_id, f"💰 PnL≈${pnl:.2f} | positions={cnt}"); return

    if low.startswith("/cycle") or low.startswith("/think"):
        if hasattr(engine, "run_cycle"): engine.run_cycle()
        tg_send(chat_id, "🔁 Ran one cycle."); return

    if low.startswith("/log"): tg_send(chat_id, events_text(20)); return

    if low.startswith("/debugwebhook"):
        info = get_webhook_info()
        last = LAST_WEBHOOK_AT.isoformat(timespec="seconds") if LAST_WEBHOOK_AT else "never"
        boot = BOOT_AT.isoformat(timespec="seconds")
        tg_send(chat_id, f"Webhook info:\n{json.dumps(info, indent=2)}\n\nboot_at={boot}\nlast_webhook_at={last}")
        return

    if low.startswith("/forcewebhook"):
        try: set_webhook(); tg_send(chat_id, "✅ Webhook re-registered.")
        except Exception as e: tg_send(chat_id, f"forcewebhook error: {e}")
        return

    if low.startswith("/forcepoll"):
        # manually trigger a polling burst immediately
        tg_send(chat_id, "⏬ Entering short polling burst now…")
        try:
            delete_webhook()
            _poll_burst()
            set_webhook()
            tg_send(chat_id, "⏫ Webhook restored.")
        except Exception as e:
            tg_send(chat_id, f"forcepoll error: {e}")
        return

    if low.startswith("/setalert"):
        new_id = (text.split(" ", 1)[1] or "").strip() if " " in text else ""
        if new_id:
            global ALERT_CHAT_ID
            ALERT_CHAT_ID = new_id
            tg_send(chat_id, f"Alerts → {new_id}")
        else:
            tg_send(chat_id, "Usage: /setalert <chat_id>")
        return

    if low.startswith("/echo"): payload = text.split(" ", 1)[1] if " " in text else "(empty)"; tg_send(chat_id, f"echo: {payload}"); return
    if low.startswith("/ping"): tg_send(chat_id, "pong"); return

    tg_send(chat_id, "Unknown command. Try /help")

# ========= WEBHOOK =========
@app.route("/webhook", methods=["POST"])
def webhook():
    """Log every update, process command, never silently ignore."""
    global LAST_WEBHOOK_AT
    LAST_WEBHOOK_AT = now_utc()

    update = request.get_json(silent=True) or {}
    msg = update.get("message") or update.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id") or "") or ADMIN_CHAT_ID

    logger.info("WEBHOOK <- chat=%s text=%r", chat_id, text)
    if ALERT_CHAT_ID and text:
        # First-time observable proof the webhook is firing
        pass  # (avoid spamming; logs above are enough)

    if not text:
        return Response("no-text", status=200)

    try:
        handle_command(chat_id, text)
    except Exception as e:
        logger.exception("command error")
        tg_send(ALERT_CHAT_ID, f"⚠️ Command error: {e}")
    return Response("ok", status=200)

# ========= POLLING BURST =========
def _poll_burst():
    global LAST_UPDATE_ID
    t0 = time.time()
    # prime offset to skip history
    try:
        primed = _api("getUpdates", timeout=0)
        if primed.get("ok"):
            for upd in primed.get("result", []):
                LAST_UPDATE_ID = max(LAST_UPDATE_ID or 0, upd.get("update_id", 0))
    except Exception as e:
        logger.warning("prime getUpdates error: %s", e)

    while time.time() - t0 < POLL_BURST_SECONDS:
        try:
            params = {"timeout": 0}
            if LAST_UPDATE_ID is not None:
                params["offset"] = LAST_UPDATE_ID + 1
            resp = _api("getUpdates", **params)
            if resp.get("ok") and resp.get("result"):
                for upd in resp["result"]:
                    LAST_UPDATE_ID = max(LAST_UPDATE_ID or 0, upd.get("update_id", 0))
                    msg = upd.get("message") or upd.get("edited_message") or {}
                    text = (msg.get("text") or "").strip()
                    chat_id = str((msg.get("chat") or {}).get("id") or "") or ADMIN_CHAT_ID
                    logger.info("POLL <- chat=%s text=%r", chat_id, text)
                    if text:
                        handle_command(chat_id, text)
        except Exception as e:
            logger.warning("getUpdates error: %s", e)
        time.sleep(POLL_INTERVAL_SEC)

# ========= SCHEDULER =========
sched = BackgroundScheduler(timezone=TZ_NAME)

def heartbeat():
    tg_send(ALERT_CHAT_ID, f"❤️ heartbeat {now_utc().isoformat(timespec='seconds')}")

def keepalive():
    if SELF_URL:
        try: requests.get(f"{SELF_URL}/healthz", timeout=8)
        except Exception: pass

def trading_cycle():
    try:
        if hasattr(engine, "run_cycle"):
            engine.run_cycle()
    except Exception as e:
        logger.exception("cycle error")
        tg_send(ALERT_CHAT_ID, f"⚠️ Cycle error: {e}")

def webhook_watchdog():
    """Auto rescue even if no webhook has *ever* arrived."""
    silent_for = (now_utc() - (LAST_WEBHOOK_AT or BOOT_AT)).total_seconds()
    if silent_for <= WATCHDOG_GRACE_SEC: return
    tg_send(ALERT_CHAT_ID, "🛟 No webhook hits — entering temporary polling burst.")
    try:
        delete_webhook()
        _poll_burst()
        set_webhook()
        tg_send(ALERT_CHAT_ID, "🔁 Webhook restored after polling burst.")
    except Exception as e:
        tg_send(ALERT_CHAT_ID, f"⚠️ Watchdog restore error: {e}")

def start_jobs():
    if not sched.running: sched.start()
    sched.add_job(heartbeat, "interval", seconds=HEARTBEAT_SEC, id="heartbeat", replace_existing=True)
    sched.add_job(trading_cycle, "interval", seconds=getattr(engine, "poll_seconds", 60), id="trading_cycle", replace_existing=True)
    sched.add_job(webhook_watchdog, "interval", seconds=120, id="wh_watchdog", replace_existing=True)
    if SELF_URL:
        sched.add_job(keepalive, "interval", seconds=300, id="keepalive", replace_existing=True)
    logger.info("Scheduler started.")

# ========= BOOT =========
def boot():
    try: set_webhook()
    except Exception as e: logger.warning("setWebhook failed at boot: %s", e)
    start_jobs()
    try:
        if ADMIN_CHAT_ID:
            tg_send(ADMIN_CHAT_ID, "✅ Boot OK (service live)")
            if hasattr(engine, "status_text"):
                tg_send(ADMIN_CHAT_ID, engine.status_text())
    except Exception: pass
    if AUTO_START and hasattr(engine, "resume"):
        try: engine.resume()
        except Exception as e: logger.warning("auto resume failed: %s", e)

boot()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
