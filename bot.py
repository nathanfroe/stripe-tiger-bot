import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from logger import log_event, log_error
from trading_engine import execute_trading_strategy
from ai_brain import retrain_model
from data_source import get_price_volume_series

# ===== Env =====
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # must be full URL, e.g. https://<service>.onrender.com/webhook
TRADE_MODE = os.getenv("TRADE_MODE", "mock").lower()

if not TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN (or BOT_TOKEN).")
if not WEBHOOK_URL:
    raise RuntimeError("Missing WEBHOOK_URL (e.g. https://<service>.onrender.com/webhook).")

# ===== Commands =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text(
            "✅ Stripe Tiger bot online.\n"
            f"Mode: {TRADE_MODE.upper()}\n"
            "Commands: /status, /retrain, /mode mock|live"
        )
        user = update.effective_user.username or update.effective_user.id
        log_event("User started bot", meta={"user": user})
    except Exception as e:
        log_error("start handler failed", meta={"error": str(e)})

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol, prices, volumes = get_price_volume_series()
        last_price = prices[-1] if prices else "N/A"
        await update.message.reply_text(
            f"📊 Tracking: {symbol}\n"
            f"Latest price: {last_price}\n"
            f"Data points: {len(prices)}"
        )
    except Exception as e:
        log_error("status handler failed", meta={"error": str(e)})
        await update.message.reply_text("⚠️ Unable to fetch status right now.")

async def retrain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        retrain_model()
        await update.message.reply_text("🧠 Model retrain triggered.")
    except Exception as e:
        log_error("retrain handler failed", meta={"error": str(e)})
        await update.message.reply_text("⚠️ Retrain failed; check logs.")

async def mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TRADE_MODE
    try:
        if context.args and context.args[0].lower() in ["mock", "live"]:
            TRADE_MODE = context.args[0].lower()
            await update.message.reply_text(f"⚙️ Trade mode set to {TRADE_MODE.upper()}")
            log_event("Trade mode updated", meta={"new_mode": TRADE_MODE})
        else:
            await update.message.reply_text("Usage: /mode mock OR /mode live")
    except Exception as e:
        log_error("mode handler failed", meta={"error": str(e)})
        await update.message.reply_text("⚠️ Could not update mode.")

# ===== Scheduled trading job =====
async def trading_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol, prices, volumes = get_price_volume_series()
        if prices and volumes:
            execute_trading_strategy(symbol, prices, volumes, TRADE_MODE)
    except Exception as e:
        log_error("trading_job failed", meta={"error": str(e)})

# ===== Main =====
def main():
    app = Application.builder().token(TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("retrain", retrain))
    app.add_handler(CommandHandler("mode", mode))

    # Run strategy every 60s (first run after 5s)
    app.job_queue.run_repeating(trading_job, interval=60, first=5)

    # Webhook server — IMPORTANT:
    # - url_path="" so we accept POSTs at "/" on the server
    # - webhook_url must be the full public URL to your webhook endpoint (in env)
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        url_path="",                 # accept at "/"
        webhook_url=WEBHOOK_URL,     # e.g. https://<service>.onrender.com/webhook
    )

if __name__ == "__main__":
    main()
