import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

def log_event(message: str):
    """Log a normal informational event for the bot."""
    logging.info(message)
    print(f"[EVENT] {datetime.now()}: {message}")

def log_error(error: str):
    """Log an error event for the bot."""
    logging.error(error)
    print(f"[ERROR] {datetime.now()}: {error}")
