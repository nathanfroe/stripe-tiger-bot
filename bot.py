import os
import time
import traceback
import schedule
from datetime import datetime
import requests

# === Environment Variables ===
TRADE_MODE = os.getenv("TRADE_MODE", "mock")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# === Telegram Alert Function ===
def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        response = requests.post(url, data=data)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

# === Heartbeat Logger ===
def heartbeat():
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    send_telegram_message(f"💓 Heartbeat — Bot is alive at {now} in {TRADE_MODE.upper()} mode.")

# === Trade Simulation (Mock) ===
def mock_trade():
    # Sample simulation for demonstration
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    decision = "BUY" if int(time.time()) % 2 == 0 else "SELL"
    message = f"📈 {decision} signal triggered at {now} (simulated)"
    send_telegram_message(message)

# === Main Bot Runner ===
def run_bot():
    try:
        heartbeat()
        mock_trade()
        # Add real trading logic here as needed
    except Exception as e:
        error_msg = f"⚠️ ERROR in bot loop: {e}\n{traceback.format_exc()}"
        send_telegram_message(error_msg)

# === Scheduler Setup ===
schedule.every(1).minutes.do(run_bot)

# === Initial Startup ===
send_telegram_message("✅ Stripe Tiger bot is live and hunting (logging mode enabled).")

while True:
    schedule.run_pending()
    time.sleep(1)
