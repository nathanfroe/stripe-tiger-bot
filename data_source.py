import os
import time
import requests
from datetime import datetime, timedelta
from logger import log_event, log_error

# Environment
# Use a single CoinGecko asset ID while we stabilize (e.g., 'ethereum', 'uniswap', 'binancecoin')
CG_ID = os.getenv("SYMBOL_CG_ID", "ethereum")
WINDOW_MINUTES = int(os.getenv("FEATURE_WINDOW_MIN", "60"))

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

def _market_chart_minutes(cg_id: str, minutes: int = 60):
    """
    Fetch minute-resolution price & volume series for the past N minutes.
    Uses /market_chart with 'days=1' and slices the tail.
    """
    try:
        url = f"{COINGECKO_BASE}/coins/{cg_id}/market_chart"
        # 1 day granularity returns ~5-min points; good enough for a first pass
        params = {"vs_currency": "usd", "days": "1"}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        prices = data.get("prices", [])               # [[ts_ms, price], ...]
        volumes = data.get("total_volumes", [])       # [[ts_ms, volume_usd], ...]

        # Convert to recent window
        cutoff = int((datetime.utcnow() - timedelta(minutes=minutes)).timestamp() * 1000)
        p = [v[1] for v in prices if v[0] >= cutoff]
        v = [v[1] for v in volumes if v[0] >= cutoff]

        # If still short (e.g., API sparse), fallback to last N points
        if len(p) < 14 or len(v) < 14:
            p = [v[1] for v in prices][-minutes:]
            v = [v[1] for v in volumes][-minutes:]

        return p, v
    except Exception as e:
        log_error("Failed to fetch market data from CoinGecko", meta={"error": str(e), "cg_id": cg_id})
        return [], []

def get_price_volume_series():
    """
    Public helper used by the trading loop.
    Returns (symbol_name, prices[], volumes[])
    """
    symbol_name = CG_ID
    prices, volumes = _market_chart_minutes(CG_ID, minutes=WINDOW_MINUTES)
    if prices and volumes:
        meta = {"symbol": symbol_name, "points": min(len(prices), len(volumes))}
        log_event("Fetched market data", level="DEBUG", meta=meta)
    else:
        log_event("No market data fetched", level="WARNING", meta={"symbol": symbol_name})
    return symbol_name, prices, volumes
