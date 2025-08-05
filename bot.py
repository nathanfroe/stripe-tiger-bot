import time
import random
import requests
import os

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TRADE_MODE = os.getenv("TRADE_MODE", "mock")

def send_telegram(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        }
        try:
            requests.post(url, data=payload)
        except Exception as e:
            print("Telegram send failed:", e)

def mock_trade_loop():
    while True:
        time.sleep(10)  # Simulate delay between checks

        fake_coin = random.choice(["ETH", "BTC", "DOGE", "UNI", "SOL"])
        fake_action = random.choice(["BUY", "SELL", "HOLD"])
        fake_price = round(random.uniform(1.0, 3000.0), 2)
        fake_profit = round(random.uniform(-50.0, 150.0), 2)

        message = f"""
📊 MOCK TRADE SIGNAL
Coin: {fake_coin}
Action: {fake_action}
Price: ${fake_price}
Simulated Profit: ${fake_profit}
"""
        send_telegram(message)
        print(message.strip())

def live_trade_loop():
    send_telegram("🔴 Live mode is not implemented yet. Staying in mock mode.")
    mock_trade_loop()

def main():
    send_telegram(f"🤖 Bot started in {TRADE_MODE.upper()} mode.")
    if TRADE_MODE == "live":
        live_trade_loop()
    else:
        mock_trade_loop()

if __name__ == "__main__":
    main()
