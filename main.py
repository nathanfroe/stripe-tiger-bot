import os
import logging
import time
from telegram import Bot
from scam_filter import is_legit_token
from token_scanner import get_new_tokens

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Telegram setup
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot = Bot(token=TELEGRAM_TOKEN)

def send_telegram_alert(message):
    try:
        bot.send_message(chat_id=CHAT_ID, text=message)
        logger.info("Telegram alert sent.")
    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")

def main_loop():
    seen = set()

    while True:
        try:
            tokens = get_new_tokens()
            for token in tokens:
                if token["address"] not in seen:
                    seen.add(token["address"])
                    logger.info(f"Token found: {token['name']} - {token['address']}")
                    if is_legit_token(token):
                        message = f"✅ Legit token detected:\nName: {token['name']}\nAddress: {token['address']}"
                        send_telegram_alert(message)
                    else:
                        logger.info(f"❌ Scam token ignored: {token['name']}")
            time.sleep(60)
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    logger.info("Stripe Tiger Bot started.")
    main_loop()
