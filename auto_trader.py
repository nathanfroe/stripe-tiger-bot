from web3 import Web3
import os
import json
import random

BSC_RPC = os.getenv("BSC_RPC")
ETH_RPC = os.getenv("ETH_RPC")
BSC_WALLET = os.getenv("BSC_WALLET")
ETH_WALLET = os.getenv("ETH_WALLET")
TRADE_MODE = os.getenv("TRADE_MODE", "mock")  # mock or live

# Routers
UNISWAP_ROUTER = "0x7a250d5630b4cf539739df2c5dacab78c659f248"  # ETH
PANCAKESWAP_ROUTER = "0x10ED43C718714eb63d5aA57B78B54704E256024E"  # BSC

web3_bsc = Web3(Web3.HTTPProvider(BSC_RPC))
web3_eth = Web3(Web3.HTTPProvider(ETH_RPC))

def trade_token(token_address, chain="bsc"):
    if TRADE_MODE == "mock":
        return mock_trade(token_address, chain)
    else:
        return live_trade(token_address, chain)

def mock_trade(token_address, chain):
    profit = round(random.uniform(-0.1, 0.25), 4)  # Simulate loss/profit
    outcome = "success" if profit > 0 else "fail"
    from ai_brain import record_trade
    record_trade(token_address, profit=profit, outcome=outcome, notes="mock")
    return {"status": outcome, "profit": profit}

def live_trade(token_address, chain):
    # TODO: Integrate actual buy logic via router ABI
    # This placeholder avoids real trades
    return {"status": "not_implemented", "token": token_address}

def get_wallet_address(chain):
    return BSC_WALLET if chain == "bsc" else ETH_WALLET

def get_web3(chain):
    return web3_bsc if chain == "bsc" else web3_eth
