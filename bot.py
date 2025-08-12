import os
import logging
import requests
from flask import Flask, request, jsonify

# -------------------------
# Config
# -------------------------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

TG_API = f"https://api.telegram.org/bot{TOKEN}"
# Optional: set this if you want Telegram to include a secret on webhook calls
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "").strip()

# -------------------------
# App
# -------------------------
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stripe-tiger-bot")

def tg_send_message(chat_id: int, text: str):
    try:
        requests.post(
            f"{TG_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
    except Exception as e:
        log.exception("sendMessage failed: %s", e)

@app.get("/")
def health():
    return jsonify(status="ok")

@app.post("/webhook")
def telegram_webhook():
    # Optional header check (only if you set WEBHOOK_SECRET_TOKEN with setWebhook)
    if WEBHOOK_SECRET_TOKEN:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET_TOKEN:
            return ("forbidden", 403)

    update = request.get_json(silent=True) or {}
    log.debug("update: %s", update)

    # Basic message handler
    msg = update.get("message") or update.get("edited_message")
    if msg:
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()

        if text.lower().startswith("/start"):
            tg_send_message(chat_id, "Stripe Tiger bot is live and hunting.")
        elif text.lower().startswith("/help"):
            tg_send_message(chat_id, "Commands: /start, /help")
        else:
            # default echo or ignore
            tg_send_message(chat_id, "Roger. 🐯")

    # (Optional) callback queries, etc., can be added here.

    return jsonify(ok=True)

# Local dev convenience
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
