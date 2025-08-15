import os
import json
import logging
import threading
import time
from logging.handlers import RotatingFileHandler
from datetime import datetime

from flask import Flask, request, jsonify

from telegram import Bot, Update, ParseMode
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters

from trade_engine import TradeEngine

# ==== Configuration from environment ====
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
WEBHOOK_URL   = os.environ.get("WEBHOOK_URL", "").strip()  # https://stripe-tiger-bot.onrender.com/webhook
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()  # optional: your numeric chat id for admin pings

if not TELEGRAM_TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN env var")

# ==== Logging (file + stdout) ====
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("stripe_tiger")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

fh = RotatingFileHandler("logs/app.log", maxBytes=1_000_000, backupCount=3)
fh.setFormatter(_fmt)
logger.addHandler(fh)

sh = logging.StreamHandler()
sh.setFormatter(_fmt)
logger.addHandler(sh)

# ==== Flask app (exposed to gunicorn as 'app') ====
app = Flask(__name__)

# ==== Telegram bot + dispatcher (webhook mode) ====
bot = Bot(token=TELEGRAM_TOKEN)
dispatcher = Dispatcher(bot, None, workers=0, use_context=True)

# ==== Trade engine (paper trading by default) ====
engine = TradeEngine(bot=bot, admin_chat_id=ADMIN_CHAT_ID or None, logger=logger)

# ---------- Telegram command handlers ----------

def cmd_start(update, context):
    update.message.reply_text("Stripe Tiger bot is live and hunting.")
    if ADMIN_CHAT_ID and str(update.effective_chat.id) != ADMIN_CHAT_ID:
        bot.send_message(chat_id=ADMIN_CHAT_ID,
                         text=f"👤 User {update.effective_user.id} invoked /start in chat {update.effective_chat.id}")

def cmd_help(update, context):
    update.message.reply_text(
        "/start – check bot is alive\n"
        "/status – account & positions summary\n"
        "/log – last 30 log lines\n"
        "/buy <SYMBOL> [USD] – market buy with USD allocation (paper by default)\n"
        "/sell <SYMBOL> [PCT] – market sell percentage of position (paper)\n"
        "/strategy – current strategy settings\n"
        "/panic – close all paper positions\n"
    )

def cmd_status(update, context):
    update.message.reply_text(engine.status_text(), parse_mode=ParseMode.MARKDOWN)

def cmd_log(update, context):
    try:
        with open("logs/app.log", "r") as f:
            lines = f.readlines()[-30:]
        text = "```\n" + "".join(lines)[-4000:] + "\n```"
        update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        update.message.reply_text(f"Failed to read log: {e}")

def cmd_strategy(update, context):
    update.message.reply_text(engine.strategy_text(), parse_mode=ParseMode.MARKDOWN)

def cmd_buy(update, context):
    args = context.args
    if not args:
        update.message.reply_text("Usage: /buy <SYMBOL> [USD]\nExample: /buy BTCUSDT 50")
        return
    symbol = args[0].upper()
    usd = float(args[1]) if len(args) > 1 else engine.cfg_allocation_usd
    msg = engine.manual_buy(symbol, usd)
    update.message.reply_text(msg)

def cmd_sell(update, context):
    args = context.args
    if not args:
        update.message.reply_text("Usage: /sell <SYMBOL> [PCT]\nExample: /sell BTCUSDT 50")
        return
    symbol = args[0].upper()
    pct = float(args[1]) if len(args) > 1 else 100.0
    msg = engine.manual_sell(symbol, pct)
    update.message.reply_text(msg)

def cmd_panic(update, context):
    msg = engine.panic_close_all()
    update.message.reply_text(msg)

def cmd_unknown(update, context):
    update.message.reply_text("Unknown command. Try /help.")

dispatcher.add_handler(CommandHandler("start",    cmd_start))
dispatcher.add_handler(CommandHandler("help",     cmd_help))
dispatcher.add_handler(CommandHandler("status",   cmd_status))
dispatcher.add_handler(CommandHandler("log",      cmd_log))
dispatcher.add_handler(CommandHandler("strategy", cmd_strategy))
dispatcher.add_handler(CommandHandler("buy",      cmd_buy))
dispatcher.add_handler(CommandHandler("sell",     cmd_sell))
dispatcher.add_handler(CommandHandler("panic",    cmd_panic))
dispatcher.add_handler(MessageHandler(Filters.command, cmd_unknown))

# ---------- Web routes ----------

@app.route("/", methods=["GET"])
def root():
    return jsonify(ok=True, service="stripe-tiger-bot", time=datetime.utcnow().isoformat() + "Z")

@app.route("/health", methods=["GET"])
def health():
    return "ok", 200

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    try:
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
    except Exception as e:
        logger.exception("Webhook processing error: %s", e)
        return "error", 500
    return "ok", 200

# ---------- Background strategy loop ----------

def strategy_loop():
    logger.info("Strategy loop started.")
    while True:
        try:
            engine.run_once()
        except Exception as e:
            logger.exception("Strategy loop error: %s", e)
            # keep going
        time.sleep(engine.cfg_poll_seconds)

def ensure_webhook():
    """Idempotently set Telegram webhook to our Render URL."""
    if not WEBHOOK_URL:
        logger.warning("WEBHOOK_URL not set. Set it in Render env to auto-manage webhook.")
        return
    try:
        info = bot.get_webhook_info()
        wanted = WEBHOOK_URL.rstrip("/")
        current = (info and info.url or "").rstrip("/")
        if current != wanted:
            bot.set_webhook(wanted)
            logger.info("Webhook set to %s", wanted)
        else:
            logger.info("Webhook already set to %s", wanted)
    except Exception as e:
        logger.warning("Could not verify/set webhook: %s", e)

# Thread start when gunicorn loads module
ensure_webhook()
t = threading.Thread(target=strategy_loop, daemon=True)
t.start()

# For local dev: python bot.py
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
