import os, math, time
from typing import Optional
from loguru import logger
from web3 import Web3

# Defaults; can override via env if you wish
UNISWAP_ROUTER = os.getenv("UNISWAP_ROUTER", "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")
PANCAKE_ROUTER = os.getenv("PANCAKE_ROUTER", "0x10ED43C718714eb63d5aA57B78B54704E256024E")
WETH = os.getenv("WETH_ADDRESS", "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
WBNB = os.getenv("WBNB_ADDRESS", "0xBB4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c")

# Minimal ABIs (only what we call)
ERC20_ABI = [
  {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
  {"constant":True,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],
   "name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
  {"constant":False,"inputs":[{"name":"spender","type":"address"},{"name":"value","type":"uint256"}],
   "name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
  {"constant":True,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf",
   "outputs":[{"name":"","type":"uint256"}],"type":"function"}
]

ROUTER_ABI = [
  {"name":"getAmountsOut","outputs":[{"name":"","type":"uint256[]"}],"inputs":[
     {"name":"amountIn","type":"uint256"},{"name":"path","type":"address[]"}],"stateMutability":"view","type":"function"},
  {"name":"swapExactETHForTokensSupportingFeeOnTransferTokens","outputs":[],
   "inputs":[{"name":"amountOutMin","type":"uint256"},{"name":"path","type":"address[]"},
             {"name":"to","type":"address"},{"name":"deadline","type":"uint256"}],
   "stateMutability":"payable","type":"function"},
  {"name":"swapExactTokensForETHSupportingFeeOnTransferTokens","outputs":[],
   "inputs":[{"name":"amountIn","type":"uint256"},{"name":"amountOutMin","type":"uint256"},
             {"name":"path","type":"address[]"},{"name":"to","type":"address"},
             {"name":"deadline","type":"uint256"}],"stateMutability":"nonpayable","type":"function"},
  {"name":"swapExactTokensForTokensSupportingFeeOnTransferTokens","outputs":[],
   "inputs":[{"name":"amountIn","type":"uint256"},{"name":"amountOutMin","type":"uint256"},
             {"name":"path","type":"address[]"},{"name":"to","type":"address"},
             {"name":"deadline","type":"uint256"}],"stateMutability":"nonpayable","type":"function"}
]

class DexExecutor:
    def __init__(self, w3_eth: Optional[Web3], w3_bsc: Optional[Web3], pk_eth: Optional[str], pk_bsc: Optional[str],
                 slippage_bps: int = 100, base_gas_limit: int = 350000):
        self.w3_eth = w3_eth
        self.w3_bsc = w3_bsc
        self.pk_eth = pk_eth
        self.pk_bsc = pk_bsc
        self.slippage_bps = slippage_bps
        self.base_gas = base_gas_limit

    def _router(self, chain: str):
        if chain == "ETH":
            return self.w3_eth.eth.contract(address=Web3.to_checksum_address(UNISWAP_ROUTER), abi=ROUTER_ABI), WETH
        else:
            return self.w3_bsc.eth.contract(address=Web3.to_checksum_address(PANCAKE_ROUTER), abi=ROUTER_ABI), WBNB

    def _account(self, chain: str):
        if chain == "ETH":
            w3, pk = self.w3_eth, self.pk_eth
        else:
            w3, pk = self.w3_bsc, self.pk_bsc
        if not (w3 and pk):
            raise RuntimeError(f"missing provider/private key for {chain}")
        acct = w3.eth.account.from_key(pk)
        return w3, pk, acct

    def _erc20(self, w3: Web3, token: str):
        return w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)

    def _get_amounts_out(self, w3: Web3, router, amount_in, path):
        return router.functions.getAmountsOut(amount_in, path).call()

    def _gas_params(self, w3: Web3):
        gas_price = w3.eth.gas_price
        return {"gas": self.base_gas, "gasPrice": gas_price}

    def _sign_send(self, w3: Web3, tx, pk: str):
        tx["nonce"] = w3.eth.get_transaction_count(tx["from"])
        signed = w3.eth.account.sign_transaction(tx, pk)
        txh = w3.eth.send_raw_transaction(signed.rawTransaction).hex()
        return txh

    # ---- BUY: base (ETH/BNB) -> token ----
    def buy(self, chain: str, token_addr: str, base_amount: float) -> str:
        router, base = self._router(chain)
        w3, pk, acct = self._account(chain)
        path = [Web3.to_checksum_address(base), Web3.to_checksum_address(token_addr)]

        # compute amountOutMin using getAmountsOut
        wei_in = int(base_amount * (10**18))
        amounts = self._get_amounts_out(w3, router, wei_in, path)
        out_min = int(amounts[-1] * (1 - self.slippage_bps / 10000))

        tx = router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
            out_min, path, acct.address, int(time.time()) + 600
        ).build_transaction({
            "from": acct.address,
            "value": wei_in,
            **self._gas_params(w3),
        })
        txh = self._sign_send(w3, tx, pk)
        return txh

    # ---- SELL: token -> base (ETH/BNB) ----
    def sell(self, chain: str, token_addr: str, usd_hint: float) -> str:
        router, base = self._router(chain)
        w3, pk, acct = self._account(chain)
        token = self._erc20(w3, token_addr)

        # use entire token balance for simplicity
        bal = token.functions.balanceOf(acct.address).call()
        if bal == 0:
            raise RuntimeError("no token balance to sell")

        # approve router if needed
        allowance = token.functions.allowance(acct.address, router.address).call()
        if allowance < bal:
            txa = token.functions.approve(router.address, bal).build_transaction({
                "from": acct.address,
                **self._gas_params(w3),
            })
            self._sign_send(w3, txa, pk)

        path = [Web3.to_checksum_address(token_addr), Web3.to_checksum_address(base)]
        amounts = self._get_amounts_out(w3, router, bal, path)
        out_min = int(amounts[-1] * (1 - self.slippage_bps / 10000))

        tx = router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
            bal, out_min, path, acct.address, int(time.time()) + 600
        ).build_transaction({
            "from": acct.address,
            **self._gas_params(w3),
        })
        txh = self._sign_send(w3, tx, pk)
        return txh
