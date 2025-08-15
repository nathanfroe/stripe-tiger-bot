import os
import logging
from flask import Flask, request, jsonify

from dotenv import load_dotenv
load_dotenv()

# --- Telegram (v13.15) ---
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters

# ---------- Config ----------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # already set in Render
if not TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN env var")

# Your live Render URL (you already confirmed this):
WEBHOOK_URL = "https://stripe-tiger-bot.onrender.com/webhook"

# ---------- App ----------
app = Flask(__name__)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("stripe-tiger-bot")

# Telegram bot + dispatcher (no polling, webhook only)
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, workers=2, use_context=True)

# ---------- Handlers ----------

def start(update, context):
    update.message.reply_text("Stripe Tiger bot is live and hunting.")

def test(update, context):
    update.message.reply_text("✅ Test OK")

def buy(update, context):
    # Placeholder logic – replace with real trading call
    args = context.args
    what = " ".join(args) if args else "token"
    update.message.reply_text(f"🛒 Buy routine triggered for: {what}")

def echo(update, context):
    update.message.reply_text(f"You said: {update.message.text}")

def error_handler(update, context):
    log.exception("Handler error", exc_info=context.error)

# Register handlers
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("test", test))
dispatcher.add_handler(CommandHandler("buy", buy, pass_args=True))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))
dispatcher.add_error_handler(error_handler)

# ---------- Flask routes ----------

@app.route("/", methods=["GET"])
def index():
    return "ok", 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify(status="ok"), 200

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    """Telegram posts updates here."""
    try:
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
    except Exception as e:
        log.exception("Webhook processing failed: %s", e)
        return "error", 500
    return "ok", 200

# Set webhook on boot (idempotent)
with app.app_context():
    try:
        bot.set_webhook(WEBHOOK_URL, allowed_updates=["message"])
        log.info("Webhook set to %s", WEBHOOK_URL)
    except Exception:
        log.exception("Failed to set webhook")

# Gunicorn entry: `bot:app`
if __name__ == "__main__":
    # Local dev only; Render uses gunicorn
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)
        
