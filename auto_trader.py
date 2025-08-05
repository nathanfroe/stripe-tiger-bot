import os
import time
import random
from ai_brain import record_trade

# Simulated trading logic for live execution

def fetch_market_price():
    return round(random.uniform(1000, 3000), 2)

def execute_trade(decision, amount):
    price = fetch_market_price()
    result = round(random.uniform(-50, 150), 2)  # Simulated profit/loss
    record_trade(decision, amount, price, result)
    return {
        "decision": decision,
        "amount": amount,
        "price": price,
        "result": result
    }

def auto_trade_loop():
    while True:
        decision = random.choice(["buy", "sell"])
        amount = round(random.uniform(0.01, 0.1), 4)
        trade_result = execute_trade(decision, amount)
        print(f"Executed {decision.upper()} | Amount: {amount} | Price: {trade_result['price']} | Result: {trade_result['result']}")
        time.sleep(60)  # Run every 60 seconds

if __name__ == "__main__":
    auto_trade_loop()
