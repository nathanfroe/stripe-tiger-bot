import os
import json
import time
import logging
from typing import Dict, List, Tuple

import numpy as np
import requests

BINANCE_BASE = "https://api.binance.com"

def _sma(arr: np.ndarray, n: int) -> np.ndarray:
    if len(arr) < n:
        return np.full_like(arr, np.nan, dtype=float)
    csum = np.cumsum(np.insert(arr, 0, 0.0))
    sma = (csum[n:] - csum[:-n]) / n
    pad = np.full(n-1, np.nan)
    return np.concatenate([pad, sma])

def _rsi(prices: np.ndarray, n: int = 14) -> float:
    if len(prices) < n + 1:
        return float("nan")
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-n:])
    avg_loss = np.mean(losses[-n:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

class TradeEngine:
    """
    Minimal, robust paper-trading engine (supports 'real' later).
    Strategy: SMA20/SMA50 cross + RSI filter on 1m data.
    """
    def __init__(self, bot, admin_chat_id=None, logger: logging.Logger = None):
        self.bot = bot
        self.admin_chat_id = admin_chat_id
        self.log = logger or logging.getLogger("engine")

        # Config via env
        symbols = os.environ.get("TRADE_SYMBOLS", "BTCUSDT,ETHUSDT")
        self.cfg_symbols: List[str] = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        self.cfg_allocation_usd: float = float(os.environ.get("ALLOCATION_USD", "50"))   # per trade
        self.cfg_poll_seconds: int = int(os.environ.get("POLL_SECONDS", "60"))
        self.cfg_mode: str = os.environ.get("TRADE_MODE", "paper").lower()  # 'paper' or 'real'

        # In-memory portfolio for paper mode
        self.cash_usdt: float = float(os.environ.get("PAPER_CASH_USDT", "1000"))
        self.positions: Dict[str, float] = {}  # symbol -> base qty

        # load persisted state
        self._load_state()

        self.log.info("Engine init | mode=%s symbols=%s cash=%.2f alloc=%.2f poll=%ss",
                      self.cfg_mode, self.cfg_symbols, self.cash_usdt, self.cfg_allocation_usd, self.cfg_poll_seconds)

    # ---------- persistence ----------
    def _state_path(self) -> str:
        return "/tmp/portfolio.json"  # survives runtime, resets on new deploy

    def _load_state(self):
        try:
            with open(self._state_path(), "r") as f:
                data = json.load(f)
            self.cash_usdt = data.get("cash_usdt", self.cash_usdt)
            self.positions = data.get("positions", {})
            self.log.info("Loaded state: cash=%.2f, positions=%s", self.cash_usdt, self.positions)
        except FileNotFoundError:
            pass
        except Exception as e:
            self.log.warning("Failed to load state: %s", e)

    def _save_state(self):
        try:
            with open(self._state_path(), "w") as f:
                json.dump({"cash_usdt": self.cash_usdt, "positions": self.positions}, f)
        except Exception as e:
            self.log.warning("Failed to save state: %s", e)

    # ---------- public API ----------
    def status_text(self) -> str:
        parts = [f"*Mode*: `{self.cfg_mode}`   *Cash*: `${self.cash_usdt:.2f}`"]
        if self.positions:
            for sym, qty in self.positions.items():
                parts.append(f"- `{sym}`: {qty:.6f}")
        else:
            parts.append("_No open positions_")
        return "\n".join(parts)

    def strategy_text(self) -> str:
        return (
            "*Strategy*\n"
            "- Timeframe: 1m klines\n"
            "- Signals: SMA20 cross SMA50 + RSI>55 buy / RSI<45 sell\n"
            f"- Symbols: `{', '.join(self.cfg_symbols)}`\n"
            f"- Allocation: `${self.cfg_allocation_usd:.2f}` per trade\n"
            f"- Poll: `{self.cfg_poll_seconds}s`\n"
        )

    def manual_buy(self, symbol: str, usd: float) -> str:
        price = self._price(symbol)
        if not np.isfinite(price):
            return f"⚠️ Price unavailable for {symbol}"
        qty = usd / price
        if self.cfg_mode == "paper":
            if self.cash_usdt < usd:
                return f"❌ Not enough cash. Cash: ${self.cash_usdt:.2f}"
            self.cash_usdt -= usd
            self.positions[symbol] = self.positions.get(symbol, 0.0) + qty
            self._save_state()
            self._notify(f"🟢 PAPER BUY {symbol} {qty:.6f} @ {price:.2f} (${usd:.2f})")
            return f"✅ Paper buy {symbol} {qty:.6f} @ {price:.2f}"
        else:
            # Placeholder for real order integration
            self._notify(f"(REAL) Would buy {symbol} ${usd:.2f}")
            return "REAL mode not implemented in this baseline."

    def manual_sell(self, symbol: str, pct: float) -> str:
        qty = self.positions.get(symbol, 0.0)
        if qty <= 0:
            return f"⚠️ No position in {symbol}"
        sell_qty = qty * (pct / 100.0)
        price = self._price(symbol)
        usd = sell_qty * price
        if self.cfg_mode == "paper":
            self.positions[symbol] = qty - sell_qty
            if self.positions[symbol] <= 1e-10:
                self.positions.pop(symbol, None)
            self.cash_usdt += usd
            self._save_state()
            self._notify(f"🔴 PAPER SELL {symbol} {sell_qty:.6f} @ {price:.2f} (${usd:.2f})")
            return f"✅ Paper sell {symbol} {sell_qty:.6f} @ {price:.2f}"
        else:
            self._notify(f"(REAL) Would sell {symbol} {pct}%")
            return "REAL mode not implemented in this baseline."

    def panic_close_all(self) -> str:
        closed = []
        for sym, qty in list(self.positions.items()):
            if qty <= 0:
                continue
            price = self._price(sym)
            usd = qty * price
            self.cash_usdt += usd
            closed.append(f"{sym} {qty:.6f} @ {price:.2f}")
            self.positions.pop(sym, None)
        self._save_state()
        if closed:
            self._notify("🟠 PANIC CLOSE:\n" + "\n".join(closed))
            return "Closed: " + ", ".join(closed)
        return "No positions to close."

    # ---------- main loop ----------
    def run_once(self):
        for sym in self.cfg_symbols:
            try:
                prices = self._klines_close(sym, interval="1m", limit=120)
                if prices is None or len(prices) < 60:
                    self.log.info("Insufficient data for %s", sym)
                    continue

                sma20 = _sma(prices, 20)
                sma50 = _sma(prices, 50)
                rsi = _rsi(prices, 14)

                last = prices[-1]
                s20, s50 = sma20[-1], sma50[-1]

                have_pos = self.positions.get(sym, 0.0) > 0.0

                # Simple signal rules
                if not have_pos and s20 > s50 and rsi >= 55.0:
                    self.manual_buy(sym, self.cfg_allocation_usd)
                elif have_pos and s20 < s50 and rsi <= 45.0:
                    self.manual_sell(sym, 100.0)

                self.log.info("Tick %s | px=%.2f sma20=%.2f sma50=%.2f rsi=%.1f pos=%.6f cash=%.2f",
                              sym, last, s20, s50, rsi, self.positions.get(sym, 0.0), self.cash_usdt)
            except Exception as e:
                self.log.exception("run_once error for %s: %s", sym, e)

    # ---------- data + helpers ----------
    def _klines_close(self, symbol: str, interval="1m", limit=120):
        url = f"{BINANCE_BASE}/api/v3/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            self.log.warning("klines %s %s -> %s", symbol, interval, r.text)
            return None
        k = r.json()
        closes = np.array([float(c[4]) for c in k], dtype=float)
        return closes

    def _price(self, symbol: str) -> float:
        url = f"{BINANCE_BASE}/api/v3/ticker/price"
        r = requests.get(url, params={"symbol": symbol}, timeout=10)
        if r.status_code != 200:
            return float("nan")
        return float(r.json()["price"])

    def _notify(self, text: str):
        try:
            if self.admin_chat_id:
                self.bot.send_message(chat_id=self.admin_chat_id, text=text)
        except Exception as e:
            self.log.warning("Notify failed: %s", e)
