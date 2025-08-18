import os
import json
import random
import logging
from datetime import datetime
from collections import deque

import requests
import pandas as pd
import numpy as np

logger = logging.getLogger("TradeMachine")

POSITIONS_FILE = os.getenv("POSITIONS_FILE", "positions.json")

# ============== Indicators ==============
def sma(series, window=14):
    return series.rolling(window=window).mean()

def ema(series, span=14):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series, window=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    rs = up.rolling(window).mean() / down.rolling(window).mean()
    return 100 - (100 / (1 + rs))

def macd(series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def bollinger(series, window=20, num_std=2):
    mean = sma(series, window)
    std = series.rolling(window).std()
    upper = mean + num_std * std
    lower = mean - num_std * std
    return upper, lower

# ============== TradeMachine Core ==============
class TradeMachine:
    def __init__(self, tg_sender=None):
        self.mode = os.getenv("TRADE_MODE", "mock")
        self.balance_usd = float(os.getenv("START_BALANCE", "10000"))
        self.positions = {}  # token-> {qty, avg_price}
        self.pnl_usd = 0.0
        self.events = deque(maxlen=50)
        self.tg_send = tg_sender

        self.eth_token = os.getenv("ETH_TOKEN_ADDRESS", "")
        self.bsc_token = os.getenv("BSC_TOKEN_ADDRESS", "")

        self.history_prices = deque(maxlen=200)
        self.load_positions()

    # ===== Persistence =====
    def save_positions(self):
        try:
            data = {
                "balance_usd": self.balance_usd,
                "pnl_usd": self.pnl_usd,
                "positions": self.positions,
            }
            with open(POSITIONS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning("Save failed: %s", e)

    def load_positions(self):
        try:
            if os.path.exists(POSITIONS_FILE):
                with open(POSITIONS_FILE, "r") as f:
                    data = json.load(f)
                self.balance_usd = data.get("balance_usd", self.balance_usd)
                self.pnl_usd = data.get("pnl_usd", 0.0)
                self.positions = data.get("positions", {})
        except Exception:
            pass

    # ===== Helpers =====
    def _log_event(self, text, send=True):
        ts = datetime.utcnow().isoformat(timespec="seconds")
        line = f"{ts} | {text}"
        self.events.append(line)
        logger.info(line)
        if send and self.tg_send:
            self.tg_send(os.getenv("ALERT_CHAT_ID", ""), line)

    def recent_events_text(self, n=12):
        return "\n".join(list(self.events)[-n:])

    def status_text(self):
        return (f"Mode={self.mode} | Bal=${self.balance_usd:.2f} | "
                f"PnL=${self.pnl_usd:.2f} | Positions={len(self.positions)}")

    # ===== Engine =====
    def run_cycle(self):
        """Called by bot scheduler."""
        # Fetch a price (mock drift if no API configured)
        price = self._get_price()

        if price:
            self.history_prices.append(price)
            self._log_event(f"Cycle price={price:.4f}", send=False)
            self.evaluate_signals(price)

        self.save_positions()

    def _get_price(self):
        if self.mode == "mock":
            if self.history_prices:
                base = self.history_prices[-1]
            else:
                base = 100.0
            drift = random.uniform(-1.5, 1.5)
            return max(1.0, base + drift)
        else:
            # TODO: Replace with real API price fetch
            return None

    def evaluate_signals(self, price):
        if len(self.history_prices) < 30:
            return

        series = pd.Series(list(self.history_prices))

        rsi_val = rsi(series).iloc[-1]
        macd_line, signal_line, hist = macd(series)
        macd_val = macd_line.iloc[-1]
        sig_val = signal_line.iloc[-1]
        upper, lower = bollinger(series)
        bb_upper, bb_lower = upper.iloc[-1], lower.iloc[-1]

        # ===== Trading Rules =====
        if rsi_val < 35 and macd_val > sig_val and price < bb_lower:
            self._log_event(f"Buy signal triggered: price={price:.2f}, rsi={rsi_val:.2f}")
            self._mock_trade("BUY", "MOCK", price)
        elif rsi_val > 65 and macd_val < sig_val and price > bb_upper:
            self._log_event(f"Sell signal triggered: price={price:.2f}, rsi={rsi_val:.2f}")
            self._mock_trade("SELL", "MOCK", price)

    # ===== Mock Trading =====
    def _mock_trade(self, side, token, price):
        qty = self.balance_usd * 0.1 / price  # 10% position sizing
        if side == "BUY":
            self.balance_usd -= qty * price
            pos = self.positions.get(token, {"qty": 0, "avg_price": 0})
            new_qty = pos["qty"] + qty
            new_avg = (pos["avg_price"] * pos["qty"] + qty * price) / new_qty
            self.positions[token] = {"qty": new_qty, "avg_price": new_avg}
            self._log_event(f"MOCK BUY {qty:.4f} {token} @ {price:.2f}")
        else:
            pos = self.positions.get(token)
            if not pos or pos["qty"] <= 0:
                self._log_event("No position to sell")
                return
            sell_qty = pos["qty"] * 0.5  # sell half
            pnl = sell_qty * (price - pos["avg_price"])
            self.pnl_usd += pnl
            self.balance_usd += sell_qty * price
            self.positions[token]["qty"] -= sell_qty
            self._log_event(f"MOCK SELL {sell_qty:.4f} {token} @ {price:.2f} | PnL={pnl:.2f}")

    # ===== Manual Commands =====
    def manual_buy(self, token):
        price = self._get_price()
        self._mock_trade("BUY", token, price)
        return f"Manual buy {token} @ {price}"

    def manual_sell(self, token):
        price = self._get_price()
        self._mock_trade("SELL", token, price)
        return f"Manual sell {token} @ {price}"

    def get_positions(self):
        out = []
        for t, p in self.positions.items():
            mv = p["qty"] * (self.history_prices[-1] if self.history_prices else p["avg_price"])
            out.append({"chain": "MOCK", "token": t, "qty": p["qty"], "avg_price": p["avg_price"], "market_value": mv})
        return out

    def set_eth_token(self, addr): self.eth_token = addr; return f"ETH token={addr}"
    def set_bsc_token(self, addr): self.bsc_token = addr; return f"BSC token={addr}"
    def set_mode(self, mode): self.mode = mode; return f"Mode set {mode}"

    def pause(self): self._log_event("Engine paused")
    def resume(self): self._log_event("Engine resumed")
