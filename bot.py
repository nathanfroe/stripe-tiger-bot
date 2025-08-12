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

async def retrain(update: Update, context: ContextTypes.DEFAULT
