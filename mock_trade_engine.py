import time
import random
from ai_brain import score_token, record_trade
from auto_trader import trade_token
from token_logger import log_token

MOCK_TOKENS = [
    {"name": "TestCoin1", "address": "0x1111", "liquidity": 15000, "holders": 5200},
    {"name": "TestCoin2", "address": "0x2222", "liquidity": 3000, "holders": 1800},
    {"name": "TestCoin3", "address": "0x3333", "liquidity": 50000, "holders": 11000},
    {"name": "TestCoin4", "address": "0x4444", "liquidity": 900, "holders": 350},
    {"name": "TestCoin5", "address": "0x5555", "liquidity": 18000, "holders": 8000},
]

def simulate_mock_trades():
    print("[AI TEST] Running mock trade simulation...")
    for token in MOCK_TOKENS:
        score = score_token(token)
        if score < 60:
            log_token(token, action="skipped", reason=f"Low score: {score}")
            continue

        result = trade_token(token["address"], chain="bsc")
        record_trade(
            token["address"],
            profit=result.get("profit", 0),
            outcome=result.get("status"),
            notes="simulation"
        )
        print(f"[SIMULATED] {token['name']} | {result}")
        time.sleep(2)

if __name__ == "__main__":
    simulate_mock_trades()
