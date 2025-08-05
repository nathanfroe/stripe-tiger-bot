from telegram.ext import CommandHandler, ApplicationBuilder

from commands import start, help_command, buy, sell, log

def register_commands(app: ApplicationBuilder):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("log", log))
