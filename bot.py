import os
import logging
from typing import Any, Dict

from flask import Flask, request, jsonify

# -------------------------------------------------
# Flask app (Gunicorn will look for "app" in bot.py)
# -------------------------------------------------
app = Flask(__name__)

# ----------------
# Basic logging
# ----------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("stripe-tiger-bot")

# -------------------------------------------------
# Optional: plug into your existing handler
#   - If you have telegram_bot.py with a handle_update(payload) function,
#     we'll call it. Otherwise we no-op safely.
# -------------------------------------------------
_update_handler = None
try:
    # Expecting a function like: def handle_update(payload: dict) -> Dict[str, Any] | None:
    from telegram_bot import handle_update as _imported_handle_update  # type: ignore

    if callable(_imported_handle_update):
        _update_handler = _imported_handle_update
        logger.info("Found telegram_bot.handle_update; will delegate webhook updates.")
except Exception as e:
    logger.info("No telegram_bot.handle_update found (that’s OK). Detail: %s", e)

# -------------------------------------------------
# Health checks
# -------------------------------------------------
@app.get("/")
def health_root():
    # Render pings / ; keep it super cheap & stable.
    return "ok", 200

@app.get("/health")
def health():
    return jsonify(status="ok"), 200


# -------------------------------------------------
# Telegram webhook endpoint
#   - Set your Telegram webhook to point here:
#     https://<your-render-hostname>/webhook
# -------------------------------------------------
@app.post("/webhook")
def telegram_webhook():
    try:
        payload: Dict[str, Any] = request.get_json(force=True, silent=False) or {}
    except Exception as e:
        logger.exception("Invalid JSON payload: %s", e)
        return jsonify(ok=False, error="invalid JSON"), 400

    # Delegate to your project’s handler if present
    if _update_handler:
        try:
            result = _update_handler(payload)
            # Your handler can return a dict for debugging/metrics; None is OK.
            if isinstance(result, dict):
                return jsonify(ok=True, handled=True, result=result), 200
            return jsonify(ok=True, handled=True), 200
        except Exception as e:
            logger.exception("Error in telegram handler: %s", e)
            return jsonify(ok=False, handled=True, error="handler error"), 500

    # Fallback: acknowledge so Telegram stops retrying
    logger.debug("No update handler wired; acknowledging payload only.")
    return jsonify(ok=True, handled=False), 200


# -------------------------------------------------
# Optional local dev entrypoint
#   - Not used on Render (Gunicorn runs the app), but handy if you run: python bot.py
# -------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
