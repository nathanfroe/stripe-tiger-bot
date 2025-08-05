import json
import os

BRAIN_FILE = "ai_brain_state.json"

def load_brain_data():
    if not os.path.exists(BRAIN_FILE):
        print("No AI brain data found.")
        return []
    with open(BRAIN_FILE, "r") as file:
        try:
            return json.load(file)
        except json.JSONDecodeError:
            print("Brain data is corrupted or empty.")
            return []

def calculate_profit(trades):
    total_profit = 0
    for trade in trades:
        total_profit += trade.get("result", 0)
    return round(total_profit, 2)

def display_summary(trades):
    if not trades:
        print("No trades logged yet.")
        return

    print("\n=== Stripe Tiger Profit Dashboard ===")
    print(f"Total Trades: {len(trades)}")
    print(f"Total Profit/Loss: {calculate_profit(trades)} USDT")
    print("----------------------------------------")
    for trade in trades[-10:]:  # Show last 10 trades
        print(f"{trade['decision'].upper()} | Amount: {trade['amount']} | Price: {trade['price']} | Result: {trade['result']}")

if __name__ == "__main__":
    trades = load_brain_data()
    display_summary(trades)
