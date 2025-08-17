import os, math
from collections import deque, defaultdict
from dataclasses import dataclass
from typing import Optional, Dict, Tuple, List
from loguru import logger
from web3 import Web3
import requests

from dex_executor import DexExecutor

# ================= ENV =================
TRADE_MODE = os.getenv("TRADE_MODE", "mock").lower()               # mock | live
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "DEX").upper()        # DEX only

RPC_URL_ETH = os.getenv("RPC_URL_ETH")
RPC_URL_BSC = os.getenv("RPC_URL_BSC")
WALLET_PRIVATE_KEY_ETH = os.getenv("WALLET_PRIVATE_KEY_ETH")
WALLET_PRIVATE_KEY_BSC = os.getenv("WALLET_PRIVATE_KEY_BSC")

ETH_TOKEN_ADDRESS = (os.getenv("ETH_TOKEN_ADDRESS") or "").strip()
BSC_TOKEN_ADDRESS = (os.getenv("BSC_TOKEN_ADDRESS") or "").strip()

UNISWAP_ROUTER = os.getenv("UNISWAP_ROUTER", "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")
PANCAKE_ROUTER = os.getenv("PANCAKE_ROUTER", "0x10ED43C718714eb63d5aA57B78B54704E256024E")

SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "100"))               # 100 = 1%
MAX_TAX_BPS  = int(os.getenv("MAX_TAX_BPS",  "300"))
MIN_LIQ_USD  = float(os.getenv("MIN_LIQ_USD","50000"))
BASE_EOA_GAS_LIMIT = int(os.getenv("BASE_EOA_GAS_LIMIT", "350000"))

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
ALLOCATION_USD = float(os.getenv("ALLOCATION_USD", os.getenv("TRADE_USD_PER_TRADE", "50")))

# Baseline (starting) thresholds
AI_MIN_PROB_BUY = float(os.getenv("AI_MIN_PROB_BUY", "0.55"))
AI_MAX_PROB_SELL = float(os.getenv("AI_MAX_PROB_SELL", "0.45"))
RSI_BUY = float(os.getenv("RSI_BUY", "55"))
RSI_SELL = float(os.getenv("RSI_SELL", "45"))
SMA_FAST = int(os.getenv("SMA_FAST", "20"))
SMA_SLOW = int(os.getenv("SMA_SLOW", "50"))

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("ADMIN_CHAT_ID")
ALERT_CHAT_ID = os.getenv("ALERT_CHAT_ID") or TELEGRAM_CHAT_ID  # fallback if provided

# Auto-tune controls
AUTO_TUNE = os.getenv("AUTO_TUNE", "true").lower() == "true"
TUNE_WARMUP = int(os.getenv("TUNE_WARMUP", "50"))      # min samples before first tune per token
TUNE_EVERY  = int(os.getenv("TUNE_EVERY",  "60"))      # tune cadence in cycles
LOCK_TUNED  = os.getenv("LOCK_TUNED", "false").lower() == "true"

# Quantiles for tuning (0..1)
AI_BUY_Q   = float(os.getenv("AI_BUY_Q",  "0.65"))
AI_SELL_Q  = float(os.getenv("AI_SELL_Q", "0.35"))
RSI_BUY_Q  = float(os.getenv("RSI_BUY_Q", "0.60"))
RSI_SELL_Q = float(os.getenv("RSI_SELL_Q","0.40"))

# ================= Data structs =================
@dataclass
class Position:
    qty: float = 0.0
    avg: float = 0.0  # average USD cost per token unit
    chain: str = ""   # "ETH" or "BSC"

class PriceWindow:
    """Keeps a rolling window of prices + RSI internals."""
    def __init__(self, rsi_len: int = 14, maxlen: int = 2000):
        self.prices = deque(maxlen=maxlen)
        self.rsi_len = rsi_len
        self._avg_gain = None
        self._avg_loss = None

    def add(self, p: float):
        if p is None:
            return
        if self.prices:
            change = p - self.prices[-1]
            gain = max(change, 0.0)
            loss = -min(change, 0.0)
            if len(self.prices) < self.rsi_len:
                if self._avg_gain is None:
                    self._avg_gain, self._avg_loss = 0.0, 0.0
                self._avg_gain += gain
                self._avg_loss += loss
            elif len(self.prices) == self.rsi_len:
                if self._avg_gain is not None:
                    self._avg_gain = (self._avg_gain + gain) / self.rsi_len
                    self._avg_loss = (self._avg_loss + loss) / self.rsi_len
            else:
                self._avg_gain = (self._avg_gain * (self.rsi_len - 1) + gain) / self.rsi_len
                self._avg_loss = (self._avg_loss * (self.rsi_len - 1) + loss) / self.rsi_len
        self.prices.append(p)

    def sma(self, n: int) -> Optional[float]:
        if len(self.prices) < n:
            return None
        window = list(self.prices)[-n:]
        return sum(window) / n

    def rsi(self) -> Optional[float]:
        if len(self.prices) < self.rsi_len + 1:
            return None
        if self._avg_loss is None or self._avg_loss == 0:
            return 100.0
        rs = self._avg_gain / self._avg_loss
        return 100 - (100 / (1 + rs))

class AdaptiveAIBrain:
    """Tiny online learner: EWMA of return signs -> [0..1] 'prob up'"""
    def __init__(self, alpha: float = 0.2, maxlen: int = 2000):
        self.alpha = alpha
        self.score = 0.5
        self.history = deque(maxlen=maxlen)  # store scores for quantiles

    def update(self, ret: float):
        signal = 0.5 + 0.5 * math.tanh(25 * ret)
        self.score = (1 - self.alpha) * self.score + self.alpha * signal
        self.history.append(self.score)

    def prob_up(self) -> float:
        return self.score

# ================= Helpers =================
def _quantile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    idx = max(0, min(len(v) - 1, int(q * (len(v) - 1))))
    return v[idx]

def _best_dexscreener_pair_usd(token_addr: str, chain: str) -> Tuple[Optional[float], Optional[float]]:
    """(price_usd, liquidity_usd) for most liquid pair of token on chain."""
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_addr}", timeout=10)
        data = r.json().get("pairs", [])
        target = "ethereum" if chain == "ETH" else "bsc"
        best = max(
            (p for p in data if p.get("chainId") == target),
            key=lambda x: float(x.get("liquidity", {}).get("usd", 0)),
            default=None,
        )
        if not best:
            return None, None
        price = float(best.get("priceUsd") or 0) or None
        liq = float(best.get("liquidity", {}).get("usd", 0) or 0)
        return price, liq
    except Exception:
        return None, None

def _base_price_usd(chain: str) -> Optional[float]:
    try:
        ids = "ethereum" if chain == "ETH" else "binancecoin"
        r = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd", timeout=10)
        return float(r.json().get(ids, {}).get("usd", 0)) or None
    except Exception:
        return None

# ================= Engine =================
class TradeMachine:
    def __init__(self, tg_sender):
        self.tg = tg_sender
        self.mode = TRADE_MODE
        self.paused = False
        self.poll_seconds = POLL_SECONDS

        self.w3_eth = Web3(Web3.HTTPProvider(RPC_URL_ETH)) if RPC_URL_ETH else None
        self.w3_bsc = Web3(Web3.HTTPProvider(RPC_URL_BSC)) if RPC_URL_BSC else None

        self.dex = DexExecutor(
            w3_eth=self.w3_eth,
            w3_bsc=self.w3_bsc,
            pk_eth=WALLET_PRIVATE_KEY_ETH,
            pk_bsc=WALLET_PRIVATE_KEY_BSC,
            slippage_bps=SLIPPAGE_BPS,
            base_gas_limit=BASE_EOA_GAS_LIMIT
        )

        self.positions: Dict[str, Position] = {}
        self.pnl_usd: float = 0.0

        # per-token history and “AI”
        self.history: Dict[str, PriceWindow] = defaultdict(lambda: PriceWindow(rsi_len=14, maxlen=2000))
        self.ai: Dict[str, AdaptiveAIBrain] = defaultdict(lambda: AdaptiveAIBrain(alpha=0.2, maxlen=2000))

        # tuned thresholds (mutable)
        self.tuned_ai_buy: Dict[str, float] = defaultdict(lambda: AI_MIN_PROB_BUY)
        self.tuned_ai_sell: Dict[str, float] = defaultdict(lambda: AI_MAX_PROB_SELL)
        self.tuned_rsi_buy: Dict[str, float] = defaultdict(lambda: RSI_BUY)
        self.tuned_rsi_sell: Dict[str, float] = defaultdict(lambda: RSI_SELL)

        # cycle counter
        self._cycle = 0

        # optional hooks
        self.log_event_cb = None  # bot.py may set: engine.log_event_cb = log_event
        self._alert_chat_id = ALERT_CHAT_ID or TELEGRAM_CHAT_ID

        # startup banner
        def _mask(addr: Optional[str]) -> str:
            if not addr or len(addr) < 10: return "MISSING"
            return f"{addr[:6]}...{addr[-4:]}"

        logger.info(
            "Engine init | mode=%s poll=%ss | ETH=%s | BSC=%s | RPC_ETH=%s | RPC_BSC=%s | autotune=%s",
            self.mode, self.poll_seconds, _mask(ETH_TOKEN_ADDRESS), _mask(BSC_TOKEN_ADDRESS),
            "yes" if RPC_URL_ETH else "no", "yes" if RPC_URL_BSC else "no", AUTO_TUNE
        )
        try:
            self._notify(
                f"🤖 Engine ready\n"
                f"• Mode: {self.mode}\n"
                f"• Poll: {self.poll_seconds}s\n"
                f"• ETH token: {_mask(ETH_TOKEN_ADDRESS)}\n"
                f"• BSC token: {_mask(BSC_TOKEN_ADDRESS)}\n"
                f"• Autotune: {AUTO_TUNE} (warmup={TUNE_WARMUP}, every={TUNE_EVERY})"
            )
        except Exception:
            pass

    # ----- controls -----
    def set_mode(self, mode: str):
        self.mode = mode
        self._notify(f"⚙️ Mode switched to {mode}")

    def pause(self):
        self.paused = True
        self._notify("⏸️ Engine paused")

    def resume(self):
        self.paused = False
        self._notify("▶️ Engine resumed")

    def set_sender(self, cb):
        try:
            if callable(cb):
                self.tg = cb
                self._notify("🔌 Sender re-wired")
        except Exception:
            pass

    def short_status(self):
        return f"mode={self.mode} paused={self.paused} positions={len(self.positions)} pnl≈{self.pnl_usd:.2f}"

    def status_text(self):
        lines = [
            f"Mode: {self.mode}",
            f"Paused: {self.paused}",
            f"Positions: {len(self.positions)}",
            f"PnL est: {self.pnl_usd:.2f}",
            f"Poll: {self.poll_seconds}s",
            f"SMA: fast={SMA_FAST} slow={SMA_SLOW}",
            f"AI tuned (buy/sell): {dict(self._ai_pairs())}",
            f"RSI tuned (buy/sell): {dict(self._rsi_pairs())}",
            f"AUTO_TUNE={AUTO_TUNE} | WARMUP={TUNE_WARMUP} | EVERY={TUNE_EVERY} | LOCK_TUNED={LOCK_TUNED}",
        ]
        return "\n".join(lines)

    def _ai_pairs(self):
        for token in list(self.tuned_ai_buy.keys()):
            yield (token, (round(self.tuned_ai_buy[token],3), round(self.tuned_ai_sell[token],3)))

    def _rsi_pairs(self):
        for token in list(self.tuned_rsi_buy.keys()):
            yield (token, (round(self.tuned_rsi_buy[token],1), round(self.tuned_rsi_sell[token],1)))

    def get_positions(self):
        out = []
        for token, pos in (self.positions or {}).items():
            out.append({
                "token": token,
                "qty": getattr(pos, "qty", 0.0),
                "avg_price": getattr(pos, "avg", None),
                "chain": getattr(pos, "chain", ""),
            })
        return out

    def _record(self, kind: str, **kw):
        try:
            cb = getattr(self, "log_event_cb", None)
            if callable(cb):
                cb(kind, **kw)
        except Exception:
            pass

    # ----- manual commands -----
    def manual_buy(self, token: str) -> str:
        chain = self._infer_chain(token)
        return self._execute(chain, "buy", token, ALLOCATION_USD)

    def manual_sell(self, token: str) -> str:
        chain = self._infer_chain(token)
        return self._execute(chain, "sell", token, ALLOCATION_USD)

    # ----- main loop -----
    def run_cycle(self):
        if self.paused:
            return
        self._cycle += 1

        tasks: List[Tuple[str, str]] = []
        if ETH_TOKEN_ADDRESS and self.w3_eth: tasks.append(("ETH", ETH_TOKEN_ADDRESS))
        if BSC_TOKEN_ADDRESS and self.w3_bsc: tasks.append(("BSC", BSC_TOKEN_ADDRESS))

        for chain, token in tasks:
            try:
                price, liq = _best_dexscreener_pair_usd(token, chain)
                if price is None or liq is None:
                    self._notify(f"⚠️ No price/liquidity for {token} on {chain}")
                    self._record("warn", token=token, chain=chain, note="no price/liquidity")
                    continue
                if liq < MIN_LIQ_USD:
                    self._notify(f"❌ Liquidity ${liq:,.0f} < min ${MIN_LIQ_USD:,.0f} for {token} on {chain}")
                    self._record("warn", token=token, chain=chain, liq=liq, note="low liq")
                    continue

                pw = self.history[token]
                prev = pw.prices[-1] if pw.prices else None
                pw.add(price)

                if prev:
                    ret = (price - prev) / prev
                    self.ai[token].update(ret)

                s_fast = pw.sma(SMA_FAST)
                s_slow = pw.sma(SMA_SLOW)
                rsi = pw.rsi()
                ai_p = self.ai[token].prob_up()

                # periodic “thinking” log (every 20 cycles)
                if self._cycle % 20 == 0:
                    self._notify(
                        f"🧠 {token} {chain} | p=${(price or 0):.6f} | "
                        f"SMA{SMA_FAST}/{SMA_SLOW}={(s_fast or 0):.6f}/{(s_slow or 0):.6f} "
                        f"| RSI={(rsi or 0):.2f} | AI={ai_p:.2f}"
                    )

                if AUTO_TUNE and not LOCK_TUNED:
                    self._maybe_autotune(token)

                ai_buy  = self.tuned_ai_buy[token]
                ai_sell = self.tuned_ai_sell[token]
                rsi_b   = self.tuned_rsi_buy[token]
                rsi_s   = self.tuned_rsi_sell[token]

                if s_fast and s_slow and rsi:
                    want_buy  = (s_fast > s_slow) and (rsi >= rsi_b) and (ai_p >= ai_buy)
                    want_sell = (s_fast < s_slow) and (rsi <= rsi_s) and (ai_p <= ai_sell)

                    if want_buy:
                        res = self._execute(chain, "buy", token, ALLOCATION_USD)
                        self._notify(
                            f"🟢 BUY {token} {chain} | p=${price:.6f} | SMA {SMA_FAST}/{SMA_SLOW}={s_fast:.6f}/{s_slow:.6f} "
                            f"| RSI={rsi:.2f}≥{rsi_b:.2f} | AI={ai_p:.2f}≥{ai_buy:.2f}\n{res}"
                        )
                        self._record("signal", token=token, chain=chain, side="buy", price=price,
                                     rsi=rsi, ai=ai_p, s_fast=s_fast, s_slow=s_slow)

                    elif want_sell:
                        res = self._execute(chain, "sell", token, ALLOCATION_USD)
                        self._notify(
                            f"🔴 SELL {token} {chain} | p=${price:.6f} | SMA {SMA_FAST}/{SMA_SLOW}={s_fast:.6f}/{s_slow:.6f} "
                            f"| RSI={rsi:.2f}≤{rsi_s:.2f} | AI={ai_p:.2f}≤{ai_sell:.2f}\n{res}"
                        )
                        self._record("signal", token=token, chain=chain, side="sell", price=price,
                                     rsi=rsi, ai=ai_p, s_fast=s_fast, s_slow=s_slow)

            except Exception as e:
                logger.exception("cycle error")
                self._notify(f"⚠️ Cycle error {chain}:{token}: {e}")
                self._record("error", token=token, chain=chain, note=f"cycle error: {e}")

    # ----- auto-tune -----
    def _maybe_autotune(self, token: str):
        pw = self.history[token]
        if len(pw.prices) < TUNE_WARMUP: return
        if self._cycle % TUNE_EVERY != 0: return

        rsi_vals = []
        snapshot = list(pw.prices)[-max(2*TUNE_WARMUP, 200):]
        tmp_pw = PriceWindow(rsi_len=14, maxlen=len(snapshot)+5)
        for p in snapshot:
            tmp_pw.add(p)
            r = tmp_pw.rsi()
            if r is not None:
                rsi_vals.append(r)

        ai_vals = list(self.ai[token].history)[-max(2*TUNE_WARMUP, 200):]

        if len(ai_vals) >= TUNE_WARMUP:
            ai_buy_q  = _quantile(ai_vals, AI_BUY_Q)
            ai_sell_q = _quantile(ai_vals, AI_SELL_Q)
            if ai_buy_q is not None and ai_sell_q is not None:
                if ai_buy_q < ai_sell_q + 0.05:
                    ai_buy_q = min(0.95, ai_sell_q + 0.05)
                self.tuned_ai_buy[token]  = round(float(ai_buy_q), 4)
                self.tuned_ai_sell[token] = round(float(ai_sell_q), 4)

        if len(rsi_vals) >= TUNE_WARMUP:
            rsi_buy_q  = _quantile(rsi_vals, RSI_BUY_Q)
            rsi_sell_q = _quantile(rsi_vals, RSI_SELL_Q)
            if rsi_buy_q is not None and rsi_sell_q is not None:
                if rsi_buy_q < rsi_sell_q + 5:
                    rsi_buy_q = min(90.0, rsi_sell_q + 5)
                self.tuned_rsi_buy[token]  = round(float(rsi_buy_q), 2)
                self.tuned_rsi_sell[token] = round(float(rsi_sell_q), 2)

        self._notify(
            f"🔧 Auto-tuned {token}: AI(buy/sell)={self.tuned_ai_buy[token]:.2f}/{self.tuned_ai_sell[token]:.2f} | "
            f"RSI(buy/sell)={self.tuned_rsi_buy[token]:.1f}/{self.tuned_rsi_sell[token]:.1f}"
        )

    # ----- core exec -----
    def _execute(self, chain: str, side: str, token_addr: str, usd_amount: float) -> str:
        if self.mode == "mock":
            price, _ = _best_dexscreener_pair_usd(token_addr, chain)
            if not price:
                return "[MOCK] no price"
            self._record("order_submitted", token=token_addr, chain=chain, side=side, price=price, usd=usd_amount, tx=None)

            pos = self.positions.get(token_addr, Position(qty=0.0, avg=0.0, chain=chain))
            if side == "buy":
                units = usd_amount / price
                new_qty = pos.qty + units
                pos.avg = (pos.avg * pos.qty + usd_amount) / new_qty if new_qty > 0 else price
                pos.qty = new_qty
                self.positions[token_addr] = pos
                self._record("fill", token=token_addr, chain=chain, side="buy", qty=units, price=price, tx=None)
            else:
                if pos.qty <= 0:
                    return "[MOCK] no position to sell"
                units = min(pos.qty, usd_amount / max(price, 1e-12))
                self.pnl_usd += units * (price - pos.avg)
                pos.qty -= units
                if pos.qty == 0:
                    pos.avg = 0.0
                self.positions[token_addr] = pos
                self._record("fill", token=token_addr, chain=chain, side="sell", qty=units, price=price, tx=None)

            return f"[MOCK] {side.upper()} {token_addr} on {chain} for ~${usd_amount:.2f} | pos={pos.qty:.6f}@{pos.avg:.6f} | PnL≈${self.pnl_usd:.2f}"

        # LIVE
        if EXECUTION_MODE != "DEX":
            return f"⚠️ Unsupported EXECUTION_MODE={EXECUTION_MODE}"

        base_price = _base_price_usd(chain)
        if not base_price:
            return "⚠️ Could not fetch base coin price"
        base_to_spend = max(1e-6, usd_amount / base_price)  # ETH or BNB

        try:
            if side == "buy":
                txh = self.dex.buy(chain, token_addr, base_to_spend)
            else:
                txh = self.dex.sell(chain, token_addr, usd_amount)

            self._record("order_submitted", token=token_addr, chain=chain, side=side, price=base_price, usd=usd_amount, tx=txh)
            self._notify(f"📝 LIVE {side.upper()} {token_addr} ({chain}) tx={txh}")
            return f"[LIVE] {side.upper()} {token_addr} on {chain} ~${usd_amount:.2f} | tx={txh}"
        except Exception as e:
            logger.exception("live exec failed")
            self._record("error", token=token_addr, chain=chain, side=side, note=f"live exec failed: {e}")
            return f"⚠️ live exec failed: {e}"

    # ----- utilities -----
    def _notify(self, text: str):
        # Preferred callback
        try:
            target = self._alert_chat_id or TELEGRAM_CHAT_ID
            if getattr(self, "tg", None) and target:
                self.tg(target, text)
                self._record("notify", text=text)
                return
        except Exception:
            pass
        # Fallback HTTP
        if TELEGRAM_CHAT_ID:
            try:
                token = os.getenv("TELEGRAM_BOT_TOKEN")
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
                    timeout=10,
                )
                self._record("notify", text=text)
            except Exception as e:
                logger.error(f"Telegram notify error: {e}")

    def _infer_chain(self, token: str) -> str:
        if BSC_TOKEN_ADDRESS and token.lower() == BSC_TOKEN_ADDRESS.lower():
            return "BSC"
        if ETH_TOKEN_ADDRESS and token.lower() == ETH_TOKEN_ADDRESS.lower():
            return "ETH"
        return "ETH"
