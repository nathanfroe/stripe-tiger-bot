import os
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# State tracker (mock for now)
mock_portfolio = {"ETH": 0, "BSC": 0}
mock_log = []

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="🐅 Stripe Tiger Bot activated.\nUse /buy /sell /log /help")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="""
/start - Activate bot
/buy - Simulate a buy order
/sell - Simulate a sell order
/log - View current mock trade logs
/help - Show commands
""")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mock_portfolio["ETH"] += 1
    mock_log.append("🟢 Buy: 1 ETH added.")
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Mock Buy Executed: 1 ETH added.")

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if mock_portfolio["ETH"] > 0:
        mock_portfolio["ETH"] -= 1
        mock_log.append("🔴 Sell: 1 ETH removed.")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Mock Sell Executed: 1 ETH removed.")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❗ Nothing to sell.")

async def log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not mock_log:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="📭 No trades yet.")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(mock_log[-5:]))

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("log", log))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, help_command))
    app.run_polling()
