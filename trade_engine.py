import os
import pandas as pd
import requests
from binance.client import Client
from logger import log_event

class TradeEngine:
    def __init__(self, api_key=None, api_secret=None):
        self.api_key = api_key or os.getenv("BINANCE_API_KEY")
        self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET")
        self.client = Client(self.api_key, self.api_secret)

    def get_historical_data(self, symbol, interval="1m", limit=50):
        """
        Pulls historical candlestick data from Binance.
        """
        try:
            klines = self.client.get_klines(symbol=symbol, interval=interval, limit=limit)
            df = pd.DataFrame(klines, columns=[
                "timestamp", "open", "high", "low", "close", "volume",
                "close_time", "quote_asset_volume", "number_of_trades",
                "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
            ])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit='ms')
            df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
            return df
        except Exception as e:
            log_event(f"❌ Error fetching historical data: {str(e)}")
            return pd.DataFrame()

    def calculate_rsi(self, series, period=14):
        """
        Calculates Relative Strength Index (RSI).
        """
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def buy(self, symbol, quantity):
        """
        Places a market buy order.
        """
        try:
            order = self.client.order_market_buy(symbol=symbol, quantity=quantity)
            log_event(f"✅ BUY order placed: {order}")
            return order
        except Exception as e:
            log_event(f"❌ Error placing BUY order: {str(e)}")
            return None

    def sell(self, symbol, quantity):
        """
        Places a market sell order.
        """
        try:
            order = self.client.order_market_sell(symbol=symbol, quantity=quantity)
            log_event(f"✅ SELL order placed: {order}")
            return order
        except Exception as e:
            log_event(f"❌ Error placing SELL order: {str(e)}")
            return None
