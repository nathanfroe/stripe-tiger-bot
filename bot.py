import os
import logging
import time
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")

# --- Bot Commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Bot is alive and running (webhook mode)!")

async def heartbeat(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="✅ Heartbeat: Bot is alive.")

# --- Main App ---
def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID or not RENDER_EXTERNAL_HOSTNAME:
        raise RuntimeError("Missing one or more required environment variables.")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    # Heartbeat every 15 minutes
    app.job_queue.run_repeating(heartbeat, interval=900, first=10)

    # --- Webhook Setup ---
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"https://{RENDER_EXTERNAL_HOSTNAME}/{TELEGRAM_TOKEN}",
    )

if __name__ == "__main__":
    main()
