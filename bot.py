import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("stripe-tiger-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g. https://<service>.onrender.com/webhook
PORT = int(os.getenv("PORT", "10000"))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Stripe Tiger bot is live and hunting.")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Mock buy executed.")

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Mock sell executed.")

async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ok")

async def on_start(app):
    # Set webhook on startup (idempotent)
    if WEBHOOK_URL:
        await app.bot.set_webhook(url=WEBHOOK_URL)
        log.info("Webhook set to %s", WEBHOOK_URL)
    else:
        log.error("WEBHOOK_URL not set; cannot receive updates.")

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("health", health))

    # IMPORTANT: webhook server only (no polling)
    app.post_init = on_start
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="",   # PTB serves the webhook at '/' when using set_webhook with full URL
    )

if __name__ == "__main__":
    main()# bot.p
