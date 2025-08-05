import json
import os
import time

LOG_FILE = "memory/trade_history.json"

def log_token(token_data, action, reason=""):
    """
    Logs token actions such as 'skipped', 'traded', 'blacklisted', etc.
    """
    log_entry = {
        "timestamp": time.time(),
        "name": token_data.get("name"),
        "address": token_data.get("address"),
        "action": action,
        "reason": reason,
        "liquidity": token_data.get("liquidity"),
        "holders": token_data.get("holders"),
    }

    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)

    logs.append(log_entry)

    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)
