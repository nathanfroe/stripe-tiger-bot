import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from logger import log_event
from trading_engine import execute_trading_strategy
from ai_brain import retrain_model
from data_source import get_price_volume_series

# Environment variables
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALERT_CHAT_ID = os.getenv("ALERT_CHAT_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
TRADE_MODE = os.getenv("TRADE_MODE", "mock").lower()

# Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Bot is online.\n"
        f"Mode: {TRADE_MODE.upper()}\n"
        "Use /status to check AI activity."
    )
    log_event("User started bot", meta={"user": update.effective_user.username})

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol, prices, volumes = get_price_volume_series()
    await update.message.reply_text(
        f"📊 Tracking {symbol}\n"
        f"Latest price: ${prices[-1] if prices else 'N/A'}\n"
        f"Data points: {len(prices)}"
    )

async def retrain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    retrain_model()
    await update.message.reply_text("🧠 AI model retrained successfully.")

async def mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TRADE_MODE
    if context.args and context.args[0].lower() in ["mock", "live"]:
        TRADE_MODE = context.args[0].lower()
        await update.message.reply_text(f"⚙️ Trade mode set to {TRADE_MODE.upper()}")
        log_event("Trade mode updated", meta={"new_mode": TRADE_MODE})
    else:
        await update.message.reply_text("Usage: /mode mock OR /mode live")

# Scheduled trading job
async def trading_job(context: ContextTypes.DEFAULT_TYPE):
    symbol, prices, volumes = get_price_volume_series()
    if prices and volumes:
        execute_trading_strategy(symbol, prices, volumes, TRADE_MODE)

def main():
    app = Application.builder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("retrain", retrain))
    app.add_handler(CommandHandler("mode", mode))

    # Job queue for periodic trading
    job_queue = app.job_queue
    job_queue.run_repeating(trading_job, interval=60, first=5)

    # Webhook mode
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 8080)),
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()
