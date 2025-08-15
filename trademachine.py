import os, time, math
from datetime import datetime
from typing import Optional
from loguru import logger
from web3 import Web3
import requests

from dex_executor import DexExecutor

# ====== ENV ======
TRADE_MODE = os.getenv("TRADE_MODE", "mock").lower()  # mock|live
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "DEX").upper()  # DEX
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))

RPC_URL_ETH = os.getenv("RPC_URL_ETH")
RPC_URL_BSC = os.getenv("RPC_URL_BSC")
WALLET_PRIVATE_KEY_ETH = os.getenv("WALLET_PRIVATE_KEY_ETH")
WALLET_PRIVATE_KEY_BSC = os.getenv("WALLET_PRIVATE_KEY_BSC")

ETH_TOKEN_ADDRESS = os.getenv("ETH_TOKEN_ADDRESS")
BSC_TOKEN_ADDRESS = os.getenv("BSC_TOKEN_ADDRESS")

SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "100"))
MIN_LIQ_USD  = float(os.getenv("MIN_LIQ_USD", "50000"))
BASE_EOA_GAS_LIMIT = int(os.getenv("BASE_EOA_GAS_LIMIT", "350000"))
ALLOCATION_USD = float(os.getenv("ALLOCATION_USD", os.getenv("TRADE_USD_PER_TRADE", "50")))

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("ADMIN_CHAT_ID")

# ====== helpers ======
def get_base_price_usd(chain: str) -> Optional[float]:
    # Fast & free: CoinGecko for ETH/BNB price
    try:
        ids = "ethereum" if chain == "ETH" else "binancecoin"
        r = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd", timeout=10)
        return r.json().get(ids, {}).get("usd")
    except Exception:
        return None

def liquidity_ok(token_addr: str, chain: str, min_liq_usd: float) -> bool:
    # DexScreener quick check
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_addr}", timeout=10)
        data = r.json().get("pairs", [])
        target_chain = "ethereum" if chain == "ETH" else "bsc"
        best = max((p for p in data if p.get("chainId")==target_chain), key=lambda x: x.get("liquidity",{}).get("usd",0), default=None)
        if not best: return False
        liq = float(best.get("liquidity", {}).get("usd", 0))
        return liq >= min_liq_usd
    except Exception:
        return False

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

        self.positions = {}  # token -> {"qty": float, "avg": float, "chain": str}

        logger.info(f"Engine init | mode={self.mode} poll={self.poll_seconds}s")

    # ----- controls -----
    def set_mode(self, mode: str):
        self.mode = mode

    def pause(self):  self.paused = True
    def resume(self): self.paused = False

    def short_status(self):
        return f"mode={self.mode} paused={self.paused} positions={len(self.positions)}"

    def status_text(self):
        return f"""Mode: {self.mode}
Paused: {self.paused}
Positions: {self.positions}
Poll: {self.poll_seconds}s
"""

    # ----- manual commands -----
    def manual_buy(self, token: str) -> str:
        chain = "ETH" if token.lower().startswith("0x") and ETH_TOKEN_ADDRESS and token.lower()==ETH_TOKEN_ADDRESS.lower() else \
                ("BSC" if BSC_TOKEN_ADDRESS and token.lower()==BSC_TOKEN_ADDRESS.lower() else "ETH")
        return self._execute(chain, "buy", token, ALLOCATION_USD)

    def manual_sell(self, token: str) -> str:
        chain = "ETH" if token.lower().startswith("0x") and ETH_TOKEN_ADDRESS and token.lower()==ETH_TOKEN_ADDRESS.lower() else \
                ("BSC" if BSC_TOKEN_ADDRESS and token.lower()==BSC_TOKEN_ADDRESS.lower() else "ETH")
        return self._execute(chain, "sell", token, ALLOCATION_USD)

    # ----- loop -----
    def run_cycle(self):
        if self.paused: return
        tasks = []
        if ETH_TOKEN_ADDRESS and self.w3_eth:
            tasks.append(("ETH", ETH_TOKEN_ADDRESS))
        if BSC_TOKEN_ADDRESS and self.w3_bsc:
            tasks.append(("BSC", BSC_TOKEN_ADDRESS))

        for chain, token in tasks:
            # toy alternation just to prove autonomous flow
            side = "buy" if int(time.time()/self.poll_seconds)%2==0 else "sell"
            res = self._execute(chain, side, token, ALLOCATION_USD)
            self.tg(TELEGRAM_CHAT_ID, res)

    # ----- execution -----
    def _execute(self, chain: str, side: str, token_addr: str, usd_amount: float) -> str:
        if not liquidity_ok(token_addr, chain, MIN_LIQ_USD):
            return f"❌ Insufficient liquidity for {token_addr} on {chain} (<${MIN_LIQ_USD:,.0f})"

        if self.mode == "mock":
            return f"[MOCK] {side.upper()} {token_addr} on {chain} for ${usd_amount}"

        if EXECUTION_MODE != "DEX":
            return f"⚠️ EXECUTION_MODE {EXECUTION_MODE} not supported"

        base_price = get_base_price_usd(chain)
        if not base_price:
            return "⚠️ could not fetch base price"
        base_to_spend = max(0.00001, usd_amount / base_price)  # ETH or BNB units

        try:
            if side == "buy":
                txh = self.dex.buy(chain, token_addr, base_to_spend)
            else:
                txh = self.dex.sell(chain, token_addr, usd_amount)  # sells up to USD worth (uses balance/price)
            return f"[LIVE] {side.upper()} {token_addr} on {chain} for ~${usd_amount} | tx={txh}"
        except Exception as e:
            logger.exception("execute")
            return f"⚠️ live exec failed: {e}"
