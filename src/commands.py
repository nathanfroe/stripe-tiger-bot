from telegram import Update
from telegram.ext import ContextTypes

# Mock account balance
mock_balance = {
    "ETH": 0
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🐅 Stripe Tiger Bot activated.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - Activate bot\n"
        "/buy - Execute mock buy\n"
        "/sell - Execute mock sell\n"
        "/log - Show recent mock actions\n"
        "/help - Show command list"
    )

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mock_balance["ETH"] += 1
    await update.message.reply_text("Mock Buy Executed: 1 ETH added.")

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mock_balance["ETH"] -= 1
    await update.message.reply_text("Mock Sell Executed: 1 ETH removed.")

async def log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🧾 Current Balance: {mock_balance['ETH']} ETH")
