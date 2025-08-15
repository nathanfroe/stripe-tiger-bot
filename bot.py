   import os, logging, threading, time, json
from datetime import datetime, timezone
from flask import Flask, request, jsonify

from dotenv import load_dotenv
load_dotenv()

# Telegram v13.x (pinned in requirements)
from telegram import Bot, Update, ParseMode
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters

from trade_engine import TradeEngine
from config import cfg

# ---------- Logging ----------
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s"
)
log = logging.getLogger("stripe_tiger")

# ---------- App / Telegram ----------
app = Flask(__name__)
bot = Bot(token=cfg.TELEGRAM_TOKEN)
disp = Dispatcher(bot, None, workers=2, use_context=True)

# ---------- Engine ----------
engine = TradeEngine(
    symbols=cfg.TRADE_SYMBOLS,
    poll_seconds=cfg.POLL_SECONDS,
    alloc_usd=cfg.ALLOCATION_USD,
    mode=cfg.TRADE_MODE,
    qty=cfg.TRADE_QTY,
    admin_chat_id=cfg.ADMIN_CHAT_ID,
    bot=bot,
    logger=log
)

# ---------- Commands ----------
def _is_admin(chat_id: int) -> bool:
    return (cfg.ADMIN_CHAT_ID is not None) and (str(chat_id) == str(cfg.ADMIN_CHAT_ID))

def cmd_start(update, context):
    update.message.reply_text("Stripe Tiger bot is live and hunting.")
def cmd_help(update, context):
    update.message.reply_text(
        "/start – alive ping\n"
        "/help – this help\n"
        "/status – engine status & positions\n"
        "/id – show your chat id\n"
        "/mode <mock|live> – switch trading mode (admin)\n"
        "/buy <SYMBOL> [USD] – manual buy (admin)\n"
        "/sell <SYMBOL> [PCT] – manual sell (admin)\n"
        "/symbols <CSV> – set symbols list (admin)\n"
        "/pause – pause engine (admin)\n"
        "/resume – resume engine (admin)\n"
        "/panic – close all paper positions (admin)\n"
    )
def cmd_id(update, context):
    update.message.reply_text(f"Your chat id: {update.effective_chat.id}")

def cmd_status(update, context):
    update.message.reply_text(engine.status_markdown(), parse_mode=ParseMode.MARKDOWN)

def cmd_mode(update, context):
    if not _is_admin(update.effective_chat.id):
        update.message.reply_text("Admin only.")
        return
    if not context.args:
        update.message.reply_text(f"Current mode: {engine.mode}")
        return
    val = context.args[0].lower 
