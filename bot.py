import os
import logging
from typing import Any, Dict

from flask import Flask, request, jsonify, abort
from telegram import Bot
from dotenv import load_dotenv

# --- Load env early (works locally; Render injects envs at runtime) ---
load_dotenv()

# --- Required envs ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN env var")

# Optional: set this in Render to harden the webhook
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()

# --- Optional trading-related envs (used by your modules if present) ---
DEX_API_KEY = os.getenv("DEX_API_KEY", "")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("stripe-tiger-bot")

# --- Telegram Bot (direct HTTP API via requests inside python-telegram-bot core) ---
bot = Bot(token=TELEGRAM_TOKEN)

# --- Flask app ---
app = Flask(__name__)

# ---- Utility: safe import + call helpers so we don't crash if modules differ ----
def _safe_import(module_name: str):
    try:
        return __import__(module_name)
    except Exception as e:
        log.warning("Optional module '%s' unavailable: %s", module_name, e)
        return None

def _safe_call(obj: Any, func_name: str, *args, **kwargs):
    """Call obj.func_name(*args, **kwargs) if present; return (ok, result|message)."""
    try:
        fn = getattr(obj, func_name, None)
        if not callable(fn):
            return False, f"{obj.__name__}.{func_name} not available"
        return True, fn(*args, **kwargs)
    except Exception as e:
        log.exception("Error calling %s.%s: %s", getattr(obj, "__name__", obj), func_name, e)
        return False, f"Error in {getattr(obj, '__name__', obj)}.{func_name}: {e}"

# Preload optional modules (only once)
token_scanner = _safe_import("token_scanner")
scam_filter = _safe_import("scam_filter")
social_filter = _safe_import("social_filter")
trade_engine = _safe_import("trade_engine")

# ---- Routes ----
@app.get("/")
def root():
    return "Stripe Tiger bot online", 200

@app.get("/health")
def health():
    return "ok", 200

@app.post("/webhook")
def webhook():
    # Optional webhook secret verification
    if WEBHOOK_SECRET:
        hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if hdr != WEBHOOK_SECRET:
            log.warning("Webhook secret mismatch: got=%r", hdr)
            abort(401)

    update: Dict[str, Any] = request.get_json(silent=True, force=True) or {}
    try:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            # Ack non-message updates quietly (joins, callbacks, etc.)
            return jsonify(ok=True)

        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()

        if not text.startswith("/"):
            # Non-command messages: ignore or add a default behavior
            return jsonify(ok=True)

        # --- Command routing ---
        parts = text.split()
        cmd = parts[0].lower()

        if cmd == "/start":
            bot.send_message(chat_id, "Stripe Tiger bot is live and hunting.")
            return jsonify(ok=True)

        if cmd == "/help":
            help_text = (
                "Commands:\n"
                "/start — bot status\n"
                "/help — this help\n"
                "/status — quick service & strategy status\n"
                "/scan <symbol|address> — analyze token\n"
                "/signals — latest trading signal snapshot\n"
                "/buy <symbol> <amount> — execute a buy (if enabled)\n"
                "/sell <symbol> <amount> — execute a sell (if enabled)\n"
                "/dashboard — link to dashboards (if configured)"
            )
            bot.send_message(chat_id, help_text)
            return jsonify(ok=True)

        if cmd == "/status":
            status_lines = ["Service: ✅ online"]
            # probe optional modules lightly
            if token_scanner: status_lines.append("Scanner: available")
            if scam_filter:   status_lines.append("Scam filter: available")
            if social_filter: status_lines.append("Social filter: available")
            if trade_engine:  status_lines.append("Trade engine: available")
            bot.send_message(chat_id, "\n".join(status_lines))
            return jsonify(ok=True)

        if cmd == "/scan":
            if len(parts) < 2:
                bot.send_message(chat_id, "Usage: /scan <symbol or address>")
                return jsonify(ok=True)

            target = parts[1]
            # Try your token_scanner first
            if token_scanner:
                ok, res = _safe_call(token_scanner, "scan_token", target)
                if ok:
                    bot.send_message(chat_id, f"Scan result for {target}:\n{res}")
                else:
                    bot.send_message(chat_id, f"Scanner fallback: {res}")
            else:
                bot.send_message(chat_id, "Scanner module not available.")
            return jsonify(ok=True)

        if cmd == "/signals":
            # Example: ask trade_engine for signals
            if trade_engine:
                ok, res = _safe_call(trade_engine, "get_latest_signals")
                if ok:
                    bot.send_message(chat_id, f"Signals:\n{res}")
                else:
                    bot.send_message(chat_id, f"Signals unavailable: {res}")
            else:
                bot.send_message(chat_id, "Trade engine not available.")
            return jsonify(ok=True)

        if cmd == "/buy":
            if len(parts) < 3:
                bot.send_message(chat_id, "Usage: /buy <symbol> <amount>")
                return jsonify(ok=True)
            symbol, amount = parts[1], parts[2]
            if trade_engine:
                ok, res = _safe_call(trade_engine, "market_buy", symbol, amount)
                if ok:
                    bot.send_message(chat_id, f"Buy executed: {res}")
                else:
                    bot.send_message(chat_id, f"Buy failed: {res}")
            else:
                bot.send_message(chat_id, "Trade engine not available.")
            return jsonify(ok=True)

        if cmd == "/sell":
            if len(parts) < 3:
                bot.send_message(chat_id, "Usage: /sell <symbol> <amount>")
                return jsonify(ok=True)
            symbol, amount = parts[1], parts[2]
            if trade_engine:
                ok, res = _safe_call(trade_engine, "market_sell", symbol, amount)
                if ok:
                    bot.send_message(chat_id, f"Sell executed: {res}")
                else:
                    bot.send_message(chat_id, f"Sell failed: {res}")
            else:
                bot.send_message(chat_id, "Trade engine not available.")
            return jsonify(ok=True)

        if cmd == "/dashboard":
            # Put your public dashboards here if you have them
            url = os.getenv("DASHBOARD_URL", "").strip()
            if url:
                bot.send_message(chat_id, f"Dashboard: {url}")
            else:
                bot.send_message(chat_id, "No dashboard configured.")
            return jsonify(ok=True)

        # Unknown command: silent/short response
        bot.send_message(chat_id, "Unknown command. Use /help.")
        return jsonify(ok=True)

    except Exception as e:
        log.exception("Webhook handler error: %s", e)
        # Return 200 so Telegram doesn't flood retries
        return jsonify(ok=True)

# Local dev runner (Render uses Gunicorn start command)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
