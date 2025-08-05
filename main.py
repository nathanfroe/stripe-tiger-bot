import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from token_scanner import get_new_tokens
from scam_filter import is_scam_token

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Stripe Tiger bot is live and hunting.")

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Scanning for new tokens...")
    tokens = get_new_tokens()
    legit_tokens = [t for t in tokens if not is_scam_token(t)]

    if not legit_tokens:
        await update.message.reply_text("No legit tokens found.")
    else:
        for token in legit_tokens[:5]:  # Limit to top 5
            msg = f"✅ Token: {token['name']}\n💧 Liquidity: ${token['liquidity']}\n🔗 Address: {token['address']}"
            await update.message.reply_text(msg)

def main():
    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise EnvironmentError("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID.")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))

    app.run_polling()

if __name__ == "__main__":
    main()
