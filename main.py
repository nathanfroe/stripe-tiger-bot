import requests
import json
import time
import os
from telegram import Bot

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
RPC_URL = os.environ.get("RPC_URL")

bot = Bot(token=TELEGRAM_TOKEN)

def check_price():
    price = 3000  # Placeholder, replace with live logic later
    return price

def notify(price):
    message = f"Current simulated ETH price is ${price}"
    bot.send_message(chat_id=CHAT_ID, text=message)

def main():
    while True:
        try:
            price = check_price()
            notify(price)
            time.sleep(60)
        except Exception as e:
            bot.send_message(chat_id=CHAT_ID, text=f"Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
