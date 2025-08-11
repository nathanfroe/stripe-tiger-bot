# bot.py
import os
import logging
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, ContextTypes
from telegram.error import TelegramError

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g., https://stripe-tiger-bot.onrender.com/webhook

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🐯 Stripe Tiger bot is live and hunting. (webhook)")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Buy command received.")

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Sell command received.")

def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    # use either the inline handlers above OR register from telegram_bot.py
    # from telegram_bot import register_handlers
    # register_handlers(app)
    from telegram.ext import CommandHandler
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    return app

if __name__ == "__main__":
    if not BOT_TOKEN or not WEBHOOK_URL:
        raise RuntimeError("Missing TELEGRAM_TOKEN or WEBHOOK_URL")
    app = build_app()
    # webhook server (single process only)
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=WEBHOOK_URL,
    )
