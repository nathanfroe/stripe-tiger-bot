import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Read Telegram Bot Token from Render environment variable
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN environment variable not found. Set it in Render dashboard.")

# Basic /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! The Stripe Tiger Bot is running successfully!")

# Entry point
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Register command handlers
    app.add_handler(CommandHandler("start", start))

    # Run the bot
    app.run_polling()

if __name__ == "__main__":
    main()
