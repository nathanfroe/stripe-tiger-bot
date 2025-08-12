import pandas as pd
import time
from ai_brain import AIBrain
from trade_engine import TradeEngine
from logger import log_event

class AutoTrader:
    def __init__(self, api_key, api_secret, symbol="BTCUSDT", interval=60):
        self.symbol = symbol
        self.interval = interval  # time between trades in seconds
        self.trade_engine = TradeEngine(api_key, api_secret)
        self.brain = AIBrain()

    def gather_market_data(self):
        """
        Get latest market data from exchange to feed into AI.
        Here you can pull indicators like moving averages, RSI, etc.
        """
        try:
            df = self.trade_engine.get_historical_data(self.symbol, "1m", 50)
            df["SMA_5"] = df["close"].rolling(window=5).mean()
            df["SMA_20"] = df["close"].rolling(window=20).mean()
            df["RSI"] = self.trade_engine.calculate_rsi(df["close"], 14)
            df = df.dropna()
            return df
        except Exception as e:
            log_event(f"❌ Error gathering market data: {str(e)}")
            return pd.DataFrame()

    def run(self):
        log_event("🤖 AutoTrader started.")
        while True:
            data = self.gather_market_data()
            if not data.empty:
                # Prepare latest row as features
                features = data[["SMA_5", "SMA_20", "RSI"]].tail(1)
                decision = self.brain.predict(features)

                if decision == 1:
                    log_event("🟢 AI says: BUY signal detected.")
                    self.trade_engine.buy(self.symbol, quantity=0.001)
                elif decision == 0:
                    log_event("🔴 AI says: SELL signal detected.")
                    self.trade_engine.sell(self.symbol, quantity=0.001)
                else:
                    log_event("⚪ AI unsure — no action.")

            time.sleep(self.interval)

if __name__ == "__main__":
    # Example API keys — replace with env vars in production
    API_KEY = "YOUR_API_KEY"
    API_SECRET = "YOUR_API_SECRET"

    trader = AutoTrader(API_KEY, API_SECRET, symbol="BTCUSDT", interval=60)
    trader.run()
