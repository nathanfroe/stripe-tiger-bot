# telegram_bot.py
"""
Webhook-safe stub. No polling, no side effects on import.
Define handlers here only if you need them, but do not start the bot.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

def register_handlers(app):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🐯 Stripe Tiger bot is live and hunting. (webhook)")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Buy command received.")

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Sell command received.")
