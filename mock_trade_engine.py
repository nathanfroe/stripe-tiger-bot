import random
import time
from ai_brain import record_trade, get_brain_summary

# Simulated mock trading for training purposes only

def simulate_mock_trade():
    decision = random.choice(["buy", "sell"])
    amount = round(random.uniform(0.01, 0.1), 4)
    price = round(random.uniform(900, 3100), 2)
    result = round(random.uniform(-25, 75), 2)
    record_trade(decision, amount, price, result)
    print(f"MOCK {decision.upper()} | Amount: {amount} | Price: {price} | P/L: {result}")

def run_mock_trading_session(duration_minutes=5):
    print("Starting mock trading session...")
    start = time.time()
    while time.time() - start < duration_minutes * 60:
        simulate_mock_trade()
        time.sleep(30)  # Run a mock trade every 30 seconds

    summary = get_brain_summary()
    print("Session Summary:", summary)

if __name__ == "__main__":
    run_mock_trading_session()
