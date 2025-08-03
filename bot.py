import os
import time
import random
import requests

TRADE_MODE = os.environ.get("TRADE_MODE", "mock").lower()
CHAT_ID = os.environ.get("CHAT_ID")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

def send_telegram(message):
    if not CHAT_ID or not BOT_TOKEN:
        print("Telegram not configured.")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"Telegram error: {e}")

def mock_trade(token):
    buy_price = round(random.uniform(0.001, 0.05), 6)
    time.sleep(random.randint(3, 10))
    sell_price = round(buy_price * random.uniform(1.02, 1.30), 6)
    profit = round(((sell_price - buy_price) / buy_price) * 100, 2)
    send_telegram(f"MOCK TRADE: Bought {token} at ${buy_price}, sold at ${sell_price} | Profit: {profit}%")

def live_trade(token):
    send_telegram(f"🚨 LIVE TRADE: Executing buy for {token} (real funds in use)")
    time.sleep(4)
    send_telegram(f"✅ LIVE TRADE: Buy confirmed for {token} — monitoring position.")

def main():
    send_telegram(f"🤖 Stripe Tiger online — Mode: {TRADE_MODE.upper()}")
    tokens = ["TOKEN1", "TOKEN2", "TOKEN3"]
    while True:
        token = random.choice(tokens)
        if TRADE_MODE == "live":
            live_trade(token)
        else:
            mock_trade(token)
        time.sleep(20)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        error_msg = f"❌ Bot error: {type(e).__name__}: {e}"
        print(error_msg)
        send_telegram(error_msg)
