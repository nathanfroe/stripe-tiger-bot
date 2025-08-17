# bot.py — webhook + APScheduler + keepalive + rich Telegram UX (MarkdownV2)

import os
import re
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
WEBHOOK_URL      = os.getenv("WEBHOOK_URL", "")                  # e.g. https://stripe-tiger-bot.onrender.com/webhook
HEARTBEAT_SEC    = int(os.getenv("HEARTBEAT_INTERVAL", "900"))
AUTO_START       = os.getenv("AUTO_START", "true").lower() == "true"
PORT             = int(os.getenv("PORT", "10000"))
SELF_URL         = os.getenv("SELF_URL", "")                     # e.g. https://stripe-tiger-bot.onrender.com
TZ_NAME          = os.getenv("TIMEZONE", "UTC")

# ===== LOGGING =====
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("bot")

# ===== Telegram send helper =====
def tg_send(chat_id: str, text: str, *, html=False):
    """Send with MarkdownV2 by default (clickable links, code blocks)."""
    if not TOKEN or not chat_id:
        return
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
        "parse_mode": "HTML" if html else "MarkdownV2",
    }
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json=payload, timeout=10)
        if r.status_code != 200:
            logger.error("sendMessage failed: %s | body=%s", r.status_code, r.text)
    except Exception as e:
        logger.exception("Telegram send error: %s", e)

def md_escape(s: str) -> str:
    """Escape string for MarkdownV2; addresses are safe but generic text may need it."""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', s)

# ===== ENGINE =====
from trademachine import (
    TradeMachine,
    _best_dexscreener_pair_usd,   # reuse helper for /price
    ETH_TOKEN_ADDRESS,
    BSC_TOKEN_ADDRESS,
)

engine = TradeMachine(tg_sender=tg_send)

# Optional: re-wire sender if method exists (no-op if absent)
try:
    if hasattr(engine, "set_sender"):
        engine.set_sender(tg_send)
        tg_send(ALERT_CHAT_ID, "🔌 Sender re\\-wired")
except Exception as e:
    logger.warning("Could not attach Telegram sender to engine: %s", e)

# ===== FLASK APP =====
app = Flask(__name__)

# ===== SCHEDULER =====
sched = BackgroundScheduler(timezone=TZ_NAME)

def heartbeat():
    ts = datetime.now(dt_tz.utc).isoformat(timespec="seconds")
    try:
        tg_send(ALERT_CHAT_ID, f"❤️ heartbeat {md_escape(ts)}")
    except Exception as e:
        logger.warning("Heartbeat send failed: %s", e)

def trading_cycle():
    try:
        if hasattr(engine, "run_cycle"):
            engine.run_cycle()
        elif hasattr(engine, "run"):
            engine.run()
        else:
            tg_send(ALERT_CHAT_ID, "⚠️ Engine has no run\\(\\)/run\\_cycle\\(\\).")
    except Exception as e:
        logger.exception("Cycle error")
        tg_send(ALERT_CHAT_ID, f"⚠️ Cycle error: {md_escape(str(e))}")

def keepalive():
    if not SELF_URL:
        return
    try:
        requests.get(f"{SELF_URL}/healthz", timeout=8)
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

# ===== WEBHOOK MGMT (one set on boot; no polling, no churn) =====
def _get_wh_info():
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo", timeout=10)
        return r.json()
    except Exception:
        return {}

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
def ensure_webhook_once():
    if not TOKEN or not WEBHOOK_URL:
        logger.warning("TOKEN or WEBHOOK_URL missing; skipping setWebhook.")
        return
    r = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/setWebhook",
        params={"url": WEBHOOK_URL, "drop_pending_updates": True, "allowed_updates": json.dumps(["message", "edited_message"])},
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

@app.route("/__selftest", methods=["POST"])
def __selftest():
    """POST JSON: {"chat_id": "<id>", "text": "/ping"} to test end-to-end."""
    data = request.get_json(silent=True) or {}
    fake = {"message": {"chat": {"id": data.get("chat_id", ADMIN_CHAT_ID)}, "text": data.get("text", "/ping")}}
    with app.test_request_context("/webhook", method="POST", json=fake):
        return webhook()

# ===== UTIL =====
ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

def fmt_link(title: str, url: str) -> str:
    # MarkdownV2 link
    return f"[{md_escape(title)}]({url})"

def code_block(s: str) -> str:
    return f"```\n{s}\n```"

def _fmt_price_line(chain: str, token_addr: str) -> str:
    if not token_addr:
        return f"{md_escape(chain)}: _(no token configured)_"
    try:
        price, liq = _best_dexscreener_pair_usd(token_addr, chain)
        if price is None or liq is None:
            return f"{md_escape(chain)}: {code_block(token_addr)} → No price/liquidity"
        return f"{md_escape(chain)}: {code_block(token_addr)} → ${price:.6f} | liq≈${liq:,.0f}"
    except Exception as e:
        logger.exception("price fetch error")
        return f"{md_escape(chain)}: error: {md_escape(str(e))}"

def require_admin(chat_id: str) -> bool:
    return str(chat_id) == str(ADMIN_CHAT_ID) if ADMIN_CHAT_ID else True

# ===== TELEGRAM WEBHOOK =====
@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True) or {}
    msg = update.get("message") or update.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id") or "") or ADMIN_CHAT_ID

    if not text:
        return Response("no-text", status=200)

    low = text.lower().strip()

    # ------- Commands -------
    if low.startswith("/start") or low.startswith("/menu") or low.startswith("/help"):
        lines = [
            "🐯 *Stripe Tiger bot is live*",
            "",
            "*Controls*",
            "• /status – current config & thresholds",
            "• /mode mock|live – switch execution",
            "• /pause  /resume",
            "• /cycle – run one analysis cycle now",
            "• /price – fetch live price/liquidity",
            "• /positions – open positions",
            "• /pnl – running PnL",
            "• /log – recent events",
        ]
        if require_admin(chat_id):
            lines += [
                "",
                "*Admin*",
                "• /seteth <address> – set ETH token",
                "• /setbsc <address> – set BSC token",
            ]
        if SELF_URL:
            lines += [
                "",
                "*Links*",
                f"• {fmt_link('Health', f'{SELF_URL}/healthz')}",
                f"• {fmt_link('Self-test', f'{SELF_URL}/__selftest')}",
            ]
        tg_send(chat_id, "\n".join(lines))
        return Response("ok", status=200)

    if low.startswith("/status"):
        try:
            if hasattr(engine, "status_text"):
                tg_send(chat_id, md_escape(engine.status_text()))
            else:
                tg_send(chat_id, "status\\_text\\(\\) not implemented.")
        except Exception as e:
            tg_send(chat_id, f"Status error: {md_escape(str(e))}")
        return Response("ok", status=200)

    if low.startswith("/mode"):
        try:
            parts = low.split()
            if len(parts) == 2 and parts[1] in ("mock", "live"):
                if hasattr(engine, "set_mode"):
                    engine.set_mode(parts[1])
                    tg_send(chat_id, f"Mode set to *{parts[1]}*")
                else:
                    tg_send(chat_id, "set\\_mode\\(\\) not implemented.")
            else:
                tg_send(chat_id, "Usage: /mode mock|live")
        except Exception as e:
            tg_send(chat_id, f"Mode error: {md_escape(str(e))}")
        return Response("ok", status=200)

    if low.startswith("/pause"):
        try:
            if hasattr(engine, "pause"):
                engine.pause()
                tg_send(chat_id, "⏸️ Engine paused")
            else:
                tg_send(chat_id, "pause\\(\\) not implemented.")
        except Exception as e:
            tg_send(chat_id, f"Pause error: {md_escape(str(e))}")
        return Response("ok", status=200)

    if low.startswith("/resume"):
        try:
            if hasattr(engine, "resume"):
                engine.resume()
                tg_send(chat_id, "▶️ Engine resumed")
            else:
                tg_send(chat_id, "resume\\(\\) not implemented.")
        except Exception as e:
            tg_send(chat_id, f"Resume error: {md_escape(str(e))}")
        return Response("ok", status=200)

    if low.startswith("/buy"):
        token = text.split(" ", 1)[1].strip() if " " in text else ""
        try:
            if hasattr(engine, "manual_buy"):
                res = engine.manual_buy(token or ETH_TOKEN_ADDRESS or BSC_TOKEN_ADDRESS or "")
                tg_send(chat_id, md_escape(res or "Buy attempted."))
            else:
                tg_send(chat_id, "manual\\_buy\\(\\) not implemented.")
        except Exception as e:
            tg_send(chat_id, f"Buy error: {md_escape(str(e))}")
        return Response("ok", status=200)

    if low.startswith("/sell"):
        token = text.split(" ", 1)[1].strip() if " " in text else ""
        try:
            if hasattr(engine, "manual_sell"):
                res = engine.manual_sell(token or ETH_TOKEN_ADDRESS or BSC_TOKEN_ADDRESS or "")
                tg_send(chat_id, md_escape(res or "Sell attempted."))
            else:
                tg_send(chat_id, "manual\\_sell\\(\\) not implemented.")
        except Exception as e:
            tg_send(chat_id, f"Sell error: {md_escape(str(e))}")
        return Response("ok", status=200)

    if low.startswith("/price"):
        lines = ["📈 *Prices \\(Dexscreener\\)*:"]
        lines.append(_fmt_price_line("ETH", getattr(engine, "eth_token", "") or ETH_TOKEN_ADDRESS))
        lines.append(_fmt_price_line("BSC", getattr(engine, "bsc_token", "") or BSC_TOKEN_ADDRESS))
        tg_send(chat_id, "\n".join(lines))
        return Response("ok", status=200)

    if low.startswith("/positions"):
        try:
            pos_lines = []
            if hasattr(engine, "get_positions"):
                for p in engine.get_positions():
                    pos_lines.append(f"{md_escape(p['chain'])} {code_block(p['token'])} qty={p['qty']:.6f} avg={p['avg_price']}")
            tg_send(chat_id, "No positions." if not pos_lines else "📦 *Positions:*\n" + "\n".join(pos_lines))
        except Exception as e:
            tg_send(chat_id, f"Positions error: {md_escape(str(e))}")
        return Response("ok", status=200)

    if low.startswith("/pnl"):
        try:
            pnl = getattr(engine, "pnl_usd", 0.0)
            count = len(getattr(engine, "positions", {}) or {})
            tg_send(chat_id, f"💰 PnL≈${pnl:.2f} | positions={count}")
        except Exception as e:
            tg_send(chat_id, f"PnL error: {md_escape(str(e))}")
        return Response("ok", status=200)

    if low.startswith("/cycle") or low.startswith("/think"):
        try:
            if hasattr(engine, "run_cycle"):
                engine.run_cycle()
                tg_send(chat_id, "🔁 Ran one cycle.")
            else:
                tg_send(chat_id, "run\\_cycle\\(\\) not implemented.")
        except Exception as e:
            tg_send(chat_id, f"Cycle error: {md_escape(str(e))}")
        return Response("ok", status=200)

    if low.startswith("/log"):
        try:
            events = getattr(engine, "events", []) or []
            if not events:
                tg_send(chat_id, "No recent events.")
            else:
                last = list(events)[-15:]
                lines = ["🗞️ *Recent events:*"]
                for e in last:
                    ts = e.get("ts","")
                    kind = e.get("kind","")
                    desc = md_escape(e.get("text") or e.get("note") or "")
                    lines.append(f"{md_escape(ts)} | {md_escape(kind)} | {desc}")
                tg_send(chat_id, "\n".join(lines))
        except Exception as e:
            tg_send(chat_id, f"Log error: {md_escape(str(e))}")
        return Response("ok", status=200)

    # ---- Admin: set tokens (full addresses, persisted in-memory) ----
    if low.startswith("/seteth"):
        if not require_admin(chat_id):
            tg_send(chat_id, "Not authorized.")
            return Response("ok", status=200)
        addr = text.split(" ", 1)[1].strip() if " " in text else ""
        if not ADDR_RE.match(addr):
            tg_send(chat_id, "❌ Invalid address. Expect 0x followed by 40 hex chars.")
            return Response("ok", status=200)
        try:
            if hasattr(engine, "set_token"):
                engine.set_token("ETH", addr)
                tg_send(chat_id, "✅ ETH token set to:\n" + code_block(addr))
            else:
                tg_send(chat_id, "set\\_token\\(\\) not implemented in engine.")
        except Exception as e:
            tg_send(chat_id, f"Set ETH error: {md_escape(str(e))}")
        return Response("ok", status=200)

    if low.startswith("/setbsc"):
        if not require_admin(chat_id):
            tg_send(chat_id, "Not authorized.")
            return Response("ok", status=200)
        addr = text.split(" ", 1)[1].strip() if " " in text else ""
        if not ADDR_RE.match(addr):
            tg_send(chat_id, "❌ Invalid address. Expect 0x followed by 40 hex chars.")
            return Response("ok", status=200)
        try:
            if hasattr(engine, "set_token"):
                engine.set_token("BSC", addr)
                tg_send(chat_id, "✅ BSC token set to:\n" + code_block(addr))
            else:
                tg_send(chat_id, "set\\_token\\(\\) not implemented in engine.")
        except Exception as e:
            tg_send(chat_id, f"Set BSC error: {md_escape(str(e))}")
        return Response("ok", status=200)

    if low.startswith("/ping"):
        tg_send(chat_id, "pong")
        return Response("ok", status=200)

    # Fallback help
    tg_send(chat_id, "Unknown command. Try /menu")
    return Response("ok", status=200)

# ===== BOOT =====
def boot():
    try:
        ensure_webhook_once()  # one gentle set; no polling
    except Exception as e:
        logger.warning("Webhook not set: %s", e)
    start_jobs()
    try:
        if ADMIN_CHAT_ID:
            tg_send(ADMIN_CHAT_ID, "✅ Boot OK \\(service live\\)")
            if hasattr(engine, "status_text"):
                tg_send(ADMIN_CHAT_ID, md_escape(engine.status_text()))
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
