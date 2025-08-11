# main.py
"""
No-op launcher to avoid starting a second Telegram process.
Keep this file because other modules may import it, but it must not start polling.
"""

if __name__ == "__main__":
    # Intentionally empty to prevent duplicate bot instances.
    # The only entrypoint should be `bot.py`.
    pass
