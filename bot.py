# bot.py — webhook + auto fallback polling (no conflicts) + diagnostics

import os, json, logging, time
from datetime import datetime, timezone as dt_tz, timedelta

import requests
from flask import Flask, request, Response
from apscheduler.schedulers.background import BackgroundScheduler
from tenacity import retry, stop_after_attempt, wait_exponential

# ===== ENV =====
TOKEN         = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("ADMIN_CHAT_ID", "")
ALERT_CHAT_ID = os.getenv("ALERT_CHAT_ID", ADMIN_CHAT_ID)
WEBHOOK_URL   = os.getenv("WEBHOOK_URL", "")
SELF_URL      = os.getenv("SELF_URL", "")
PORT          = int(os.getenv("PORT", "10000"))
TZ_NAME       = os.getenv("TIMEZONE", "UTC")
HEARTBEAT_SEC = int(os.getenv("HEARTBEAT_INTERVAL", "900"))
AUTO_START    = os.getenv("AUTO_START", "true").lower() == "true"

# Fallback polling knobs
WATCHDOG_GRACE_SEC   = int(os.getenv("WEBHOOK_WATCHDOG_SEC", "300"))     # 5 min no webhook → fallback
POLL_BURST_SECONDS   = int(os.getenv("POLL_BURST_SECONDS", "30"))        # poll for 30s then restore webhook
POLL_INTERVAL_SEC    = int(os.getenv("POLL_INTERVAL_SEC", "2"))          # call getUpdates every 2s during burst

# ===== LOGGING =====
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("bot")

def now_utc():
    return datetime.now(dt_tz.utc)

# track webhook health
LAST_WEBHOOK_AT = None
LAST_UPDATE_ID  = None
IS_IN_POLL_BURST = False

# ===== Telegram helpers (plain text) =====
def tg_send(chat_id: str, text: str):
    if not TOKEN or not chat_id:
        return
    if text and len(text) > 4000:
        text = text[:3990] + "\n…[truncated]"
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id": str(chat_id), "text": text}, timeout=12)
    if r.status_code != 200:
        logger.error("sendMessage failed: %s | %s", r.status_code, r.text)

def api(method: str, **params):
    r = requests.get(f"https://api.telegram.org/bot{TOKEN}/{method}", params=params, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"{method} failed: {r.text}")
    return r.json()

def get_webhook_info():
    try:
        return api("getWebhookInfo")
    except Exception as e:
        logger.warning("getWebhookInfo error: %s", e)
        return {}

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=30))
def set_webhook():
    if not TOKEN or not WEBHOOK_URL:
        logger.warning("TOKEN or WEBHOOK_URL missing; skip setWebhook.")
        return
    r = requests.get(f"https://api.telegram.org/bot{TOKEN}/setWebhook",
                     params={"url": WEBHOOK_URL,
                             "drop_pending_updates": True,
                             "allowed_updates": json.dumps(["message","edited_message"])},
                     timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"setWebhook failed: {r.text}")
    logger.info("Webhook set OK: %s", get_webhook_info())

def delete_webhook():
    try:
        api("deleteWebhook", drop_pending_updates=False)
        logger.info("Webhook deleted (temporary polling mode enabled).")
    except Exception as e:
        logger.warning("deleteWebhook error: %s", e)

# ===== ENGINE =====
from trademachine import TradeMachine, _best_dexscreener_pair_usd
engine = TradeMachine(tg_sender=tg_send)

def set_alert_chat(cid: str):
    global ALERT_CHAT_ID
    ALERT_CHAT_ID = str(cid)
    try:
        engine._alert_chat_id = ALERT_CHAT_ID
    except Exception:
        pass

# ===== FLASK =====
app = Flask(__name__)

@app.route("/", methods=["GET"])
def root(): return Response("OK", status=200)

@app.route("/healthz", methods=["GET"])
def healthz(): return Response("healthy", status=200)

# server-side self test to inject a fake update
@app.route("/__selftest", methods=["POST"])
def __selftest():
    data = request.get_json(silent=True) or {}
    fake = {"message": {"chat": {"id": data.get("chat_id", ADMIN_CHAT_ID)},
                        "text": data.get("text", "/ping")}}
    with app.test_request_context("/webhook", method="POST", json=fake):
        return webhook()

# ===== UTIL =====
def fmt_price_line(chain: str, addr: str|None) -> str:
    if not addr: return f"{chain}: (no token configured)"
    try:
        p, liq = _best_dexscreener_pair_usd(addr, chain)
        if p is None or liq is None:
            return f"{chain}: {addr} → No price/liquidity"
        return f"{chain}: {addr} → ${p:.6f} | liq≈${liq:,.0f}"
    except Exception as e:
        return f"{chain}: error: {e}"

def events_text(n=20) -> str:
    ev = getattr(engine, "events", [])[-n:]
    if not ev: return "No recent events."
    lines = ["🧾 Recent events:"]
    for e in ev:
        ts, kind = e.get("ts",""), e.get("kind","")
        rest = {k:v for k,v in e.items() if k not in ("ts","kind")}
        lines.append(f"{ts} | {kind} | {rest}")
    return "\n".join(lines)

# ===== WEBHOOK HANDLER =====
@app.route("/webhook", methods=["POST"])
def webhook():
    global LAST_WEBHOOK_AT
    LAST_WEBHOOK_AT = now_utc()
    logger.info("Webhook hit at %s", LAST_WEBHOOK_AT.isoformat(timespec="seconds"))

    update = request.get_json(silent=True) or {}
    msg = update.get("message") or update.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id") or "") or ADMIN_CHAT_ID
    if not text: return Response("no-text", status=200)

    handle_command(chat_id, text)
    return Response("ok", status=200)

# ===== COMMANDS =====
def handle_command(chat_id: str, text: str):
    low = text.lower()

    if low.startswith(("/start","/help","/menu")):
        tg_send(chat_id,
            "🐯 Stripe Tiger bot is live.\n\n"
            "Commands:\n"
            "/status /price /positions /pnl /cycle /log\n"
            "/buy <addr> /sell <addr>\n"
            "/seteth <addr> /setbsc <addr>\n"
            "/mode mock|live /pause /resume\n"
            "/diag /debugwebhook /forcewebhook /setalert <chat_id>\n"
            "/echo <text> /ping"
        )
        if hasattr(engine, "status_text"): tg_send(chat_id, engine.status_text()); return

    if low.startswith("/status"):
        tg_send(chat_id, engine.status_text() if hasattr(engine,"status_text") else "status_text() missing"); return

    if low.startswith("/mode"):
        parts = low.split()
        if len(parts)==2 and parts[1] in ("mock","live"):
            engine.set_mode(parts[1]) if hasattr(engine,"set_mode") else None
            tg_send(chat_id, f"Mode set to {parts[1]}")
        else:
            tg_send(chat_id, "Usage: /mode mock|live"); return
        return

    if low.startswith("/pause"):  engine.pause() if hasattr(engine,"pause") else None; tg_send(chat_id,"Engine paused"); return
    if low.startswith("/resume"): engine.resume() if hasattr(engine,"resume") else None; tg_send(chat_id,"Engine resumed"); return

    if low.startswith("/seteth"):
        addr = text.split(" ",1)[1].strip() if " " in text else ""
        engine.set_token("ETH", addr); tg_send(chat_id, f"ETH token set to {addr}"); return
    if low.startswith("/setbsc"):
        addr = text.split(" ",1)[1].strip() if " " in text else ""
        engine.set_token("BSC", addr); tg_send(chat_id, f"BSC token set to {addr}"); return

    if low.startswith("/buy"):
        addr = text.split(" ",1)[1].strip() if " " in text else ""
        res = engine.manual_buy(addr) if hasattr(engine,"manual_buy") else "manual_buy() missing"
        tg_send(chat_id, res); return

    if low.startswith("/sell"):
        addr = text.split(" ",1)[1].strip() if " " in text else ""
        res = engine.manual_sell(addr) if hasattr(engine,"manual_sell") else "manual_sell() missing"
        tg_send(chat_id, res); return

    if low.startswith("/price"):
        tg_send(chat_id, "📈 Prices:\n" +
               "\n".join([fmt_price_line("ETH", getattr(engine,"eth_token",None)),
                          fmt_price_line("BSC", getattr(engine,"bsc_token",None))])); return

    if low.startswith("/positions"):
        pos = []
        if hasattr(engine,"get_positions"):
            for p in engine.get_positions():
                pos.append(f"{p['chain']} {p['token']} qty={p['qty']:.6f} avg={p['avg_price']}")
        tg_send(chat_id, "No positions." if not pos else "📦 Positions:\n"+"\n".join(pos)); return

    if low.startswith("/pnl"):
        pnl = getattr(engine,"pnl_usd",0.0); cnt = len(getattr(engine,"positions",{}) or {})
        tg_send(chat_id, f"💰 PnL≈${pnl:.2f} | positions={cnt}"); return

    if low.startswith("/cycle") or low.startswith("/think"):
        engine.run_cycle() if hasattr(engine,"run_cycle") else None; tg_send(chat_id,"🔁 Ran one cycle."); return

    if low.startswith("/log"): tg_send(chat_id, events_text(20)); return

    if low.startswith("/debugwebhook"):
        info = get_webhook_info()
        last = LAST_WEBHOOK_AT.isoformat(timespec="seconds") if LAST_WEBHOOK_AT else "never"
        tg_send(chat_id, f"Webhook info:\n{json.dumps(info,indent=2)}\n\nlast_webhook_at={last}"); return

    if low.startswith("/forcewebhook"):
        try:
            set_webhook(); tg_send(chat_id,"✅ Webhook re-registered.")
        except Exception as e:
            tg_send(chat_id, f"forcewebhook error: {e}")
        return

    if low.startswith("/setalert"):
        new_id = (text.split(" ",1)[1] or "").strip() if " " in text else ""
        if new_id: set_alert_chat(new_id); tg_send(chat_id, f"Alerts → {new_id}")
        else: tg_send(chat_id,"Usage: /setalert <chat_id>"); return

    if low.startswith("/echo"): payload = text.split(" ",1)[1] if " " in text else "(empty)"; tg_send(chat_id, f"echo: {payload}"); return
    if low.startswith("/ping"): tg_send(chat_id, "pong"); return

    tg_send(chat_id, "Unknown command. Try /help")

# ===== SCHEDULER JOBS =====
sched = BackgroundScheduler(timezone=TZ_NAME)

def heartbeat():
    tg_send(ALERT_CHAT_ID, f"❤️ heartbeat {now_utc().isoformat(timespec='seconds')}")

def keepalive():
    if SELF_URL:
        try: requests.get(f"{SELF_URL}/healthz", timeout=8)
        except Exception: pass

def trading_cycle():
    try:
        engine.run_cycle() if hasattr(engine,"run_cycle") else None
    except Exception as e:
        logger.exception("cycle error")
        tg_send(ALERT_CHAT_ID, f"⚠️ Cycle error: {e}")

def webhook_watchdog():
    """No webhook hits for WATCHDOG_GRACE_SEC → temporary polling burst."""
    global IS_IN_POLL_BURST
    if IS_IN_POLL_BURST or not TOKEN:
        return
    if LAST_WEBHOOK_AT is None:
        return
    if (now_utc() - LAST_WEBHOOK_AT).total_seconds() <= WATCHDOG_GRACE_SEC:
        return

    # Enter polling burst
    IS_IN_POLL_BURST = True
    tg_send(ALERT_CHAT_ID, "🛟 No webhook hits recently — entering temporary polling burst.")
    try:
        delete_webhook()
        _poll_burst()
    except Exception as e:
        logger.warning("poll burst error: %s", e)
    finally:
        try:
            set_webhook()
            tg_send(ALERT_CHAT_ID, "🔁 Webhook restored after polling burst.")
        except Exception as e:
            tg_send(ALERT_CHAT_ID, f"⚠️ Failed to restore webhook: {e}")
        IS_IN_POLL_BURST = False

def _poll_burst():
    """Short-lived long-polling loop (no webhook set) to process pending messages."""
    global LAST_UPDATE_ID
    t0 = time.time()
    while time.time() - t0 < POLL_BURST_SECONDS:
        try:
            params = {"timeout": 0}
            if LAST_UPDATE_ID is not None:
                params["offset"] = LAST_UPDATE_ID + 1
            resp = api("getUpdates", **params)
            if resp.get("ok") and resp.get("result"):
                for upd in resp["result"]:
                    LAST_UPDATE_ID = max(LAST_UPDATE_ID or 0, upd.get("update_id", 0))
                    msg = upd.get("message") or upd.get("edited_message") or {}
                    text = (msg.get("text") or "").strip()
                    chat_id = str((msg.get("chat") or {}).get("id") or "") or ADMIN_CHAT_ID
                    if text:
                        handle_command(chat_id, text)
        except Exception as e:
            logger.warning("getUpdates error: %s", e)
        time.sleep(POLL_INTERVAL_SEC)

def start_jobs():
    if not sched.running:
        sched.start()
    sched.add_job(heartbeat, "interval", seconds=HEARTBEAT_SEC, id="heartbeat", replace_existing=True)
    sched.add_job(trading_cycle, "interval", seconds=getattr(engine,"poll_seconds",60), id="trading_cycle", replace_existing=True)
    sched.add_job(webhook_watchdog, "interval", seconds=120, id="wh_watchdog", replace_existing=True)
    if SELF_URL:
        sched.add_job(keepalive, "interval", seconds=300, id="keepalive", replace_existing=True)
    logger.info("Scheduler started.")

# ===== BOOT =====
def boot():
    try: set_webhook()
    except Exception as e: logger.warning("setWebhook failed at boot: %s", e)
    start_jobs()
    try:
        if ADMIN_CHAT_ID:
            tg_send(ADMIN_CHAT_ID, "✅ Boot OK (service live)")
            if hasattr(engine, "status_text"): tg_send(ADMIN_CHAT_ID, engine.status_text())
    except Exception: pass
    if AUTO_START:
        try: engine.resume() if hasattr(engine,"resume") else None
        except Exception as e: logger.warning("auto resume failed: %s", e)

boot()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
