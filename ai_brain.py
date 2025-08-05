import json
import os
import time

BRAIN_FILE = "memory/ai_brain.json"

def load_brain():
    if not os.path.exists(BRAIN_FILE):
        return {}
    with open(BRAIN_FILE, "r") as f:
        return json.load(f)

def save_brain(data):
    with open(BRAIN_FILE, "w") as f:
        json.dump(data, f, indent=2)

def record_trade(token, profit, outcome, notes=""):
    brain = load_brain()
    if token not in brain:
        brain[token] = {
            "trades": [],
            "success": 0,
            "failure": 0,
            "total_profit": 0
        }

    trade = {
        "timestamp": time.time(),
        "profit": profit,
        "outcome": outcome,
        "notes": notes
    }

    brain[token]["trades"].append(trade)
    brain[token]["total_profit"] += profit

    if outcome == "success":
        brain[token]["success"] += 1
    else:
        brain[token]["failure"] += 1

    save_brain(brain)

def get_brain_summary():
    brain = load_brain()
    summary = {}
    for token, data in brain.items():
        total = data["success"] + data["failure"]
        win_rate = (data["success"] / total) * 100 if total > 0 else 0
        summary[token] = {
            "win_rate": round(win_rate, 2),
            "trades": len(data["trades"]),
            "profit": data["total_profit"]
        }
    return summary
