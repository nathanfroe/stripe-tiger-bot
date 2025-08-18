# trademachine.py — Full engine (mock + live DEX), indicators, auto-tune (safe),
# structured events, manual commands, and chat-level config controls.

import os
import math
import json
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Tuple, List, Any
from collections import deque, defaultdict
from datetime import datetime, timezone as dt_tz

import requests

# ======== ENV ========
TRADE_MODE         = os.getenv("TRADE_MODE", "mock").lower()            # mock | live
EXECUTION_MODE     = os.getenv("EXECUTION_MODE", "DEX").upper()         # only DEX wired
POLL_SECONDS       = int(os.getenv("POLL_SECONDS", "60"))               # bot scheduler reads this
ALLOCATION_USD     = float(os.getenv("ALLOCATION_USD", "50"))           # per trade (mock & live)

ETH_TOKEN_ADDRESS  = (os.getenv("ETH_TOKEN_ADDRESS") or "").strip()
BSC_TOKEN_ADDRESS  = (os.getenv("BSC_TOKEN_ADDRESS") or "").strip()

RPC_URL_ETH             = os.getenv("RPC_URL_ETH")
RPC_URL_BSC             = os.getenv("RPC_URL_BSC")
WALLET_PRIVATE_KEY_ETH  = os.getenv("WALLET_PRIVATE_KEY_ETH")
WALLET_PRIVATE_KEY_BSC  = os.getenv("WALLET_PRIVATE_KEY_BSC")

SLIPPAGE_BPS       = int(os.getenv("SLIPPAGE_BPS", "100"))              # 100 = 1%
BASE_EOA_GAS_LIMIT = int(os.getenv("BASE_EOA_GAS_LIMIT", "350000"))

MIN_LIQ_USD        = float(os.getenv("MIN_LIQ_USD","50000"))

SMA_FAST           = int(os.getenv("SMA_FAST", "20"))
SMA_SLOW           = int(os.getenv("SMA_SLOW", "50"))
RSI_LEN            = int(os.getenv("RSI_LEN", "14"))

AI_MIN_PROB_BUY    = float(os.getenv("AI_MIN_PROB_BUY", "0.55"))
AI_MAX_PROB_SELL   = float(os.getenv("AI_MAX_PROB_SELL", "0.45"))
RSI_BUY            = float(os.getenv("RSI_BUY", "55"))
RSI_SELL           = float(os.getenv("RSI_SELL", "45"))

AUTO_TUNE          = os.getenv("AUTO_TUNE", "true").lower() == "true"
TUNE_WARMUP        = int(os.getenv("TUNE_WARMUP", "50"))
TUNE_EVERY         = int(os.getenv("TUNE_EVERY",  "60"))                # every N cycles
LOCK_TUNED         = os.getenv("LOCK_TUNED", "false").lower() == "true"

AI_BUY_Q           = float(os.getenv("AI_BUY_Q",  "0.65"))
AI_SELL_Q          = float(os.getenv("AI_SELL_Q", "0.35"))
RSI_BUY_Q          = float(os.getenv("RSI_BUY_Q", "0.60"))
RSI_SELL_Q         = float(os.getenv("RSI_SELL_Q","0.40"))

TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("ADMIN_CHAT_ID")
ALERT_CHAT_ID      = os.getenv("ALERT_CHAT_ID") or TELEGRAM_CHAT_ID

# ======== LOGGING ========
log = logging.getLogger("trademachine")
if not log.handlers:
    logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)

# ======== HELPERS ========
def _now_iso() -> str:
    return datetime.now(dt_tz.utc).isoformat(timespec="seconds")

def _safe_round(x, n=6):
    try:
        return round(float(x), n)
    except Exception:
        return x

def _quantile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    idx = max(0, min(len(v) - 1, int(q * (len(v) - 1))))
    return v[idx]

DEX_API = "https://api.dexscreener.com/latest/dex/tokens"

def _best_dexscreener_pair_usd(token: str, chain: str) -> Tuple[Optional[float], Optional[float]]:
    """Return (price_usd, liquidity_usd) for best-known pair of token (by liquidity)."""
    token = (token or "").strip()
    if not token:
        return None, None
    try:
        r = requests.get(f"{DEX_API}/{token}", timeout=12)
        data = r.json()
        pairs = data.get("pairs") or []
        if not pairs:
            return None, None
        best, best_liq = None, -1.0
        for p in pairs:
            liq = float(p.get("liquidity", {}).get("usd", 0.0) or 0.0)
            if liq > best_liq:
                best, best_liq = p, liq
        if not best:
            return None, None
        price = float(best.get("priceUsd") or 0.0) or None
        return price, (best_liq if best_liq > 0 else None)
    except Exception as e:
        log.warning("Dexscreener error: %s", e)
        return None, None

def _base_price_usd(chain: str) -> Optional[float]:
    """USD price of base coin (ETH or BNB) for LIVE sizing."""
    try:
        ids = "ethereum" if chain == "ETH" else "binancecoin"
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd",
            timeout=10,
        )
        return float(r.json().get(ids, {}).get("usd", 0)) or None
    except Exception:
        return None

def _sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

def _rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        delta = values[-i] - values[-i - 1]
        if delta >= 0:
            gains.append(delta)
        else:
            losses.append(-delta)
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

# ======== DATA ========
@dataclass
class Position:
    qty: float = 0.0
    avg: float = 0.0
    chain: str = ""     # "ETH" or "BSC"
    opened_at: str = ""

class PriceWindow:
    def __init__(self, rsi_len: int = RSI_LEN, maxlen: int = 2000):
        self.prices = deque(maxlen=maxlen)
        self.rsi_len = rsi_len
    def add(self, p: Optional[float]):
        if p:
            self.prices.append(float(p))
    def sma(self, n: int) -> Optional[float]:
        return _sma(list(self.prices), n)
    def rsi(self) -> Optional[float]:
        return _rsi(list(self.prices), self.rsi_len)

class AdaptiveAIBrain:
    def __init__(self, alpha: float = 0.2, maxlen: int = 2000):
        self.alpha = alpha
        self.score = 0.5
        self.history = deque(maxlen=maxlen)
    def update(self, ret: float):
        sig = 0.5 + 0.5 * math.tanh(25 * ret)
        self.score = (1 - self.alpha) * self.score + self.alpha * sig
        self.history.append(self.score)
    def prob_up(self) -> float:
        return self.score

# ======== OPTIONAL LIVE EXECUTOR ========
DexExecutor = None
try:
    from dex_executor import DexExecutor as _DexExec
    DexExecutor = _DexExec
except Exception:
    pass

try:
    from web3 import Web3
except Exception:
    Web3 = None

# ======== ENGINE ========
class TradeMachine:
    def __init__(self, tg_sender=None):
        # wiring
        self._send = tg_sender or (lambda chat, txt: None)
        self.mode = TRADE_MODE
        self.poll_seconds = max(15, POLL_SECONDS)
        self._paused = False

        # tokens
        self.eth_token = ETH_TOKEN_ADDRESS
        self.bsc_token = BSC_TOKEN_ADDRESS

        # indicators
        self.sma_fast = SMA_FAST
        self.sma_slow = SMA_SLOW
        self.rsi_len  = RSI_LEN

        # tuned thresholds (start at baseline; may be tuned)
        self.tuned_ai_buy: Dict[str, float]  = defaultdict(lambda: AI_MIN_PROB_BUY)
        self.tuned_ai_sell: Dict[str, float] = defaultdict(lambda: AI_MAX_PROB_SELL)
        self.tuned_rsi_buy: Dict[str, float] = defaultdict(lambda: RSI_BUY)
        self.tuned_rsi_sell: Dict[str, float] = defaultdict(lambda: RSI_SELL)

        # rings
        self.history: Dict[str, PriceWindow] = defaultdict(lambda: PriceWindow(rsi_len=self.rsi_len, maxlen=2000))
        self.ai: Dict[str, AdaptiveAIBrain]  = defaultdict(lambda: AdaptiveAIBrain(alpha=0.2, maxlen=2000))

        # positions & pnl
        self.positions: Dict[str, Position] = {}
        self.pnl_usd: float = 0.0

        # cycles & events
        self._cycle = 0
        self._events: deque[str] = deque(maxlen=200)

        # LIVE dex executor wiring
        self.slippage_bps = SLIPPAGE_BPS
        self.min_liq_usd  = MIN_LIQ_USD
        self.dex = None
        if self.mode == "live" and DexExecutor:
            self._wire_live_executor()

        self._log_event(
            f"🤖 Engine init | mode={self.mode} | poll={self.poll_seconds}s | "
            f"ETH={self._mask(self.eth_token)} BSC={self._mask(self.bsc_token)} | "
            f"autotune={AUTO_TUNE} | minliq=${int(self.min_liq_usd)} | slip={self.slippage_bps}bps"
        )

    # ----- internal helpers -----
    def _mask(self, s: Optional[str]) -> str:
        s = s or ""
        return f"{s[:6]}...{s[-4:]}" if len(s) > 12 else (s or "(none)")

    def _log_event(self, text: str):
        stamp = datetime.now().strftime('%H:%M:%S')
        self._events.append(f"{stamp} | {text}")

    def _notify(self, text: str):
        try:
            if callable(self._send) and (TELEGRAM_CHAT_ID or ALERT_CHAT_ID):
                self._send(ALERT_CHAT_ID or TELEGRAM_CHAT_ID, text)
        except Exception:
            pass

    # ----- live wiring & checks -----
    def _wire_live_executor(self):
        """Create DexExecutor with Web3 providers when available."""
        try:
            w3_eth = Web3(Web3.HTTPProvider(RPC_URL_ETH)) if (Web3 and RPC_URL_ETH) else None
            w3_bsc = Web3(Web3.HTTPProvider(RPC_URL_BSC)) if (Web3 and RPC_URL_BSC) else None
            self.dex = DexExecutor(
                w3_eth=w3_eth,
                w3_bsc=w3_bsc,
                pk_eth=WALLET_PRIVATE_KEY_ETH,
                pk_bsc=WALLET_PRIVATE_KEY_BSC,
                slippage_bps=self.slippage_bps,
                base_gas_limit=BASE_EOA_GAS_LIMIT,
            )
            self._log_event("🔗 Live DEX executor wired.")
        except Exception as e:
            self.dex = None
            self._log_event(f"⚠️ Live executor wiring failed: {e}")

    def live_ready_report(self) -> str:
        """Human-readable checklist for LIVE mode."""
        checks = []
        ok = lambda b: "✅" if b else "❌"
        checks.append(f"{ok(bool(DexExecutor))} dex_executor present")
        checks.append(f"{ok(Web3 is not None)} web3 import")
        checks.append(f"{ok(bool(RPC_URL_ETH or RPC_URL_BSC))} RPC url(s)")
        checks.append(f"{ok(bool(WALLET_PRIVATE_KEY_ETH or WALLET_PRIVATE_KEY_BSC))} private key(s)")
        checks.append(f"{ok(self.dex is not None)} DexExecutor wired")
        checks.append(f"{ok(self.eth_token or self.bsc_token)} token configured")
        checks.append(f"{ok(self.slippage_bps>0)} slippage={self.slippage_bps}bps")
        checks.append(f"{ok(self.min_liq_usd>=0)} min_liq=${int(self.min_liq_usd)}")
        return "LIVE readiness:\n" + "\n".join("• " + c for c in checks)

    # ----- admin & config -----
    def set_sender(self, fn):
        if callable(fn):
            self._send = fn
            self._log_event("🔌 sender re-wired")

    def pause(self):
        self._paused = True
        self._log_event("⏸️ paused")
        self._notify("⏸️ Engine paused")

    def resume(self):
        self._paused = False
        self._log_event("▶️ resumed")
        self._notify("▶️ Engine resumed")

    def set_mode(self, m: str):
        self.mode = "live" if m == "live" else "mock"
        if self.mode == "live" and DexExecutor and self.dex is None:
            self._wire_live_executor()
        self._log_event(f"⚙️ mode={self.mode}")
        self._notify(f"⚙️ Mode set to {self.mode}")

    def set_eth_token(self, addr: str) -> str:
        self.eth_token = (addr or "").strip()
        self._log_event(f"ETH token set to {self.eth_token or '(none)'}")
        return f"ETH token set to {self.eth_token or '(none)'}"

    def set_bsc_token(self, addr: str) -> str:
        self.bsc_token = (addr or "").strip()
        self._log_event(f"BSC token set to {self.bsc_token or '(none)'}")
        return f"BSC token set to {self.bsc_token or '(none)'}"

    def set_allocation(self, usd: float) -> str:
        global ALLOCATION_USD
        ALLOCATION_USD = max(1.0, float(usd))
        self._log_event(f"Allocation set to ${ALLOCATION_USD:.2f}")
        return f"Allocation set to ${ALLOCATION_USD:.2f}"

    def set_poll(self, seconds: int) -> str:
        self.poll_seconds = max(10, int(seconds))
        self._log_event(f"Poll set to {self.poll_seconds}s")
        return f"Poll set to {self.poll_seconds}s"

    def set_slippage(self, bps: int) -> str:
        self.slippage_bps = max(1, int(bps))
        if self.dex:
            try:
                # re-wire to apply new slippage on the executor (implementation dependent)
                self._wire_live_executor()
            except Exception:
                pass
        self._log_event(f"Slippage set to {self.slippage_bps}bps")
        return f"Slippage set to {self.slippage_bps}bps"

    def set_min_liq(self, usd: float) -> str:
        self.min_liq_usd = max(0.0, float(usd))
        self._log_event(f"Min liquidity set to ${self.min_liq_usd:,.0f}")
        return f"Min liquidity set to ${self.min_liq_usd:,.0f}"

    # ----- reporting -----
    def short_status(self) -> str:
        return f"mode={self.mode} paused={self._paused} positions={len(self.positions)} pnl≈{_safe_round(self.pnl_usd,2)}"

    def status_text(self) -> str:
        lines = [
            f"Mode: {self.mode}",
            f"Paused: {self._paused}",
            f"Positions: {len(self.positions)}",
            f"PnL est: {_safe_round(self.pnl_usd, 2)}",
            f"Poll: {self.poll_seconds}s",
            f"ETH token: {self.eth_token or '(none)'}",
            f"BSC token: {self.bsc_token or '(none)'}",
            f"SMA: fast={self.sma_fast} slow={self.sma_slow}",
            f"RSI len: {self.rsi_len}",
            f"Slippage: {self.slippage_bps} bps | MinLiq: ${int(self.min_liq_usd)}",
            f"AUTO_TUNE={AUTO_TUNE} | WARMUP={TUNE_WARMUP} | EVERY={TUNE_EVERY} | LOCK_TUNED={LOCK_TUNED}",
        ]
        if self.mode == "live":
            lines.append(self.live_ready_report())
        return "\n".join(lines)

    def recent_events_text(self, n: int = 12) -> str:
        if not self._events:
            return "No recent events."
        return "🗞️ Recent events:\n" + "\n".join(list(self._events)[-n:])

    def get_positions(self) -> List[Dict[str, Any]]:
        out = []
        for token, pos in self.positions.items():
            price, _ = _best_dexscreener_pair_usd(token, pos.chain)
            mv = (price or 0.0) * pos.qty
            out.append({
                "chain": pos.chain,
                "token": token,
                "qty": pos.qty,
                "avg_price": pos.avg,
                "market_value": mv,
            })
        return out

    # ----- main loop -----
    def run_cycle(self):
        if self._paused:
            return
        self._cycle += 1

        tasks: List[Tuple[str, str]] = []
        if self.eth_token:
            tasks.append(("ETH", self.eth_token))
        if self.bsc_token:
            tasks.append(("BSC", self.bsc_token))

        if not tasks:
            if self._cycle % 10 == 1:
                self._log_event("No tokens configured. Use /seteth or /setbsc.")
            return

        for chain, token in tasks:
            try:
                price, liq = _best_dexscreener_pair_usd(token, chain)
                if price is None or liq is None:
                    self._log_event(f"⚠️ {chain} {self._mask(token)}: no price/liquidity")
                    continue
                if liq < self.min_liq_usd:
                    self._log_event(f"❌ {chain} {self._mask(token)} liq ${_safe_round(liq,0)} < min ${_safe_round(self.min_liq_usd,0)}")
                    continue

                pw = self.history[token]
                prev = pw.prices[-1] if pw.prices else None
                pw.add(price)

                if prev:
                    ret = (price - prev) / prev
                    self.ai[token].update(ret)

                s_fast = pw.sma(self.sma_fast)
                s_slow = pw.sma(self.sma_slow)
                rsi    = pw.rsi()
                ai_p   = self.ai[token].prob_up()

                if self._cycle % 20 == 0:
                    self._log_event(
                        f"🧠 {chain} {self._mask(token)} p=${_safe_round(price,6)} "
                        f"SMA{self.sma_fast}/{self.sma_slow}={_safe_round(s_fast,6)}/{_safe_round(s_slow,6)} "
                        f"RSI={_safe_round(rsi,2)} AI={_safe_round(ai_p,2)}"
                    )

                if AUTO_TUNE and not LOCK_TUNED:
                    self._maybe_autotune(token)

                ai_buy  = self.tuned_ai_buy[token]
                ai_sell = self.tuned_ai_sell[token]
                rsi_b   = self.tuned_rsi_buy[token]
                rsi_s   = self.tuned_rsi_sell[token]

                sig_buy  = s_fast and s_slow and rsi and (s_fast > s_slow) and (rsi >= rsi_b) and (ai_p >= ai_buy)
                sig_sell = s_fast and s_slow and rsi and (s_fast < s_slow) and (rsi <= rsi_s) and (ai_p <= ai_sell)

                key = token
                have_pos = key in self.positions and self.positions[key].qty > 1e-12

                if sig_buy and not have_pos:
                    res = self._execute(chain, "buy", token, ALLOCATION_USD)
                    self._log_event(f"🟢 BUY {chain} {self._mask(token)} @ ${_safe_round(price,6)} | {res}")

                elif sig_sell and have_pos:
                    res = self._execute(chain, "sell", token, ALLOCATION_USD)
                    self._log_event(f"🔴 SELL {chain} {self._mask(token)} @ ${_safe_round(price,6)} | {res}")

            except Exception as e:
                log.exception("cycle error")
                self._log_event(f"⚠️ cycle error {chain}:{self._mask(token)}: {e}")
                self._notify(f"⚠️ cycle error {chain}:{self._mask(token)}: {e}")

    # ----- auto-tune -----
    def _maybe_autotune(self, token: str):
        pw = self.history[token]
        if len(pw.prices) < TUNE_WARMUP:
            return
        if self._cycle % TUNE_EVERY != 0:
            return

        snapshot = list(pw.prices)[-max(2*TUNE_WARMUP, 180):]
        rsi_vals: List[float] = []
        tmp = PriceWindow(rsi_len=self.rsi_len, maxlen=len(snapshot)+5)
        for p in snapshot:
            tmp.add(p)
            r = tmp.rsi()
            if r is not None:
                rsi_vals.append(r)

        ai_vals = list(self.ai[token].history)[-max(2*TUNE_WARMUP, 180):]

        changed = False
        if len(ai_vals) >= TUNE_WARMUP:
            ai_b = _quantile(ai_vals, AI_BUY_Q)
            ai_s = _quantile(ai_vals, AI_SELL_Q)
            if ai_b is not None and ai_s is not None:
                if ai_b < ai_s + 0.05:
                    ai_b = min(0.95, ai_s + 0.05)
                self.tuned_ai_buy[token]  = round(float(ai_b), 4)
                self.tuned_ai_sell[token] = round(float(ai_s), 4)
                changed = True

        if len(rsi_vals) >= TUNE_WARMUP:
            r_b = _quantile(rsi_vals, RSI_BUY_Q)
            r_s = _quantile(rsi_vals, RSI_SELL_Q)
            if r_b is not None and r_s is not None:
                if r_b < r_s + 5:
                    r_b = min(90.0, r_s + 5)
                self.tuned_rsi_buy[token]  = round(float(r_b), 2)
                self.tuned_rsi_sell[token] = round(float(r_s), 2)
                changed = True

        if changed:
            self._log_event(
                f"🔧 tuned {self._mask(token)} AI={self.tuned_ai_buy[token]:.2f}/{self.tuned_ai_sell[token]:.2f} "
                f"RSI={self.tuned_rsi_buy[token]:.1f}/{self.tuned_rsi_sell[token]:.1f}"
            )

    # ----- execution (mock + live) -----
    def _execute(self, chain: str, side: str, token_addr: str, usd_amount: float) -> str:
        key = token_addr
        price, _ = _best_dexscreener_pair_usd(token_addr, chain)
        if not price:
            return "[no price]"

        if self.mode == "mock":
            pos = self.positions.get(key, Position(qty=0.0, avg=0.0, chain=chain, opened_at=""))
            if side == "buy":
                units = usd_amount / max(price, 1e-9)
                new_qty = pos.qty + units
                pos.avg = (pos.avg * pos.qty + usd_amount) / new_qty if new_qty > 0 else price
                pos.qty = new_qty
                pos.chain = chain
                if not pos.opened_at:
                    pos.opened_at = _now_iso()
                self.positions[key] = pos
                return f"[MOCK FILL] buy {units:.6f} @ ${_safe_round(price,6)} pos={pos.qty:.6f}@{_safe_round(pos.avg,6)}"
            else:
                if pos.qty <= 0:
                    return "[MOCK] no position"
                units = min(pos.qty, usd_amount / max(price, 1e-12))
                self.pnl_usd += units * (price - pos.avg)
                pos.qty -= units
                if pos.qty <= 0:
                    self.positions.pop(key, None)
                    return f"[MOCK FILL] sell {units:.6f} @ ${_safe_round(price,6)} | flat | PnL+={_safe_round(self.pnl_usd,2)}"
                else:
                    self.positions[key] = pos
                    return f"[MOCK FILL] sell {units:.6f} @ ${_safe_round(price,6)} | rem={pos.qty:.6f}"

        # LIVE path
        if EXECUTION_MODE != "DEX" or not DexExecutor:
            return "[LIVE disabled or dex_executor missing]"
        if self.dex is None:
            self._wire_live_executor()
            if self.dex is None:
                return "[LIVE not ready: executor not wired]"

        # compute base amount (ETH/BNB) to spend for BUY
        try:
            if side == "buy":
                base_usd = _base_price_usd(chain)
                if not base_usd:
                    return "[LIVE] base coin USD price unavailable"
                base_amt = max(1e-9, usd_amount / base_usd)  # ETH or BNB
                txh = self.dex.buy(chain, token_addr, base_amt)  # assumes your DexExecutor expects base amount
                self._notify(f"📝 LIVE BUY {chain} {self._mask(token_addr)} tx={txh}")
                return f"[LIVE] buy ${_safe_round(usd_amount,2)} → base≈{_safe_round(base_amt,6)} | tx={txh}"
            else:
                # If your DexExecutor expects USD for sell, this matches your earlier signature.
                txh = self.dex.sell(chain, token_addr, usd_amount)
                self._notify(f"📝 LIVE SELL {chain} {self._mask(token_addr)} tx={txh}")
                return f"[LIVE] sell ~${_safe_round(usd_amount,2)} | tx={txh}"
        except Exception as e:
            log.exception("live exec failed")
            return f"[LIVE ERROR] {e}"

    # ----- manual -----
    def manual_buy(self, token: str) -> str:
        t = (token or self.eth_token or self.bsc_token or "").strip()
        if not t:
            return "Provide token: /buy <token_address> or configure ETH_TOKEN_ADDRESS/BSC_TOKEN_ADDRESS"
        chain = "ETH" if t.lower()==(self.eth_token or "").lower() else ("BSC" if t.lower()==(self.bsc_token or "").lower() else "ETH")
        return self._execute(chain, "buy", t, ALLOCATION_USD)

    def manual_sell(self, token: str) -> str:
        t = (token or self.eth_token or self.bsc_token or "").strip()
        if not t:
            return "Provide token: /sell <token_address>"
        chain = "ETH" if t.lower()==(self.eth_token or "").lower() else ("BSC" if t.lower()==(self.bsc_token or "").lower() else "ETH")
        return self._execute(chain, "sell", t, ALLOCATION_USD)
