import json
import os
from flask import Flask, jsonify

app = Flask(__name__)

BRAIN_FILE = "memory/ai_brain.json"
TRADE_LOG_FILE = "memory/trade_history.json"

@app.route("/profit")
def profit():
    if not os.path.exists(BRAIN_FILE):
        return jsonify({"error": "No brain data yet."})

    with open(BRAIN_FILE) as f:
        brain = json.load(f)

    return jsonify(brain)

@app.route("/trades")
def trades():
    if not os.path.exists(TRADE_LOG_FILE):
        return jsonify({"error": "No trade log found."})

    with open(TRADE_LOG_FILE) as f:
        log = json.load(f)

    return jsonify(log)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
