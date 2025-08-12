import numpy as np
from datetime import datetime, timedelta
from logger import log_event, log_error

def extract_features(price_data: list[float], volume_data: list[float], window: int = 14) -> list[float]:
    """
    Convert raw market data into numerical features for the AI model.

    :param price_data: List of recent prices (most recent last).
    :param volume_data: List of recent volumes (most recent last).
    :param window: Lookback window for indicators.
    :return: Feature vector [price_change, sma, rsi, vol_change, volatility]
    """
    try:
        if len(price_data) < window or len(volume_data) < window:
            log_event("Not enough data to extract features", level="WARNING")
            return [0.0] * 5

        # Price change (percentage)
        price_change = (price_data[-1] - price_data[-window]) / price_data[-window]

        # Simple moving average (SMA)
        sma = np.mean(price_data[-window:])

        # RSI calculation
        gains = []
        losses = []
        for i in range(-window + 1, 0):
            change = price_data[i] - price_data[i - 1]
            if change > 0:
                gains.append(change)
            else:
                losses.append(abs(change))
        avg_gain = np.mean(gains) if gains else 0.0001
        avg_loss = np.mean(losses) if losses else 0.0001
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        # Volume change
        vol_change = (volume_data[-1] - volume_data[-window]) / volume_data[-window]

        # Volatility (standard deviation of returns)
        returns = [ (price_data[i] - price_data[i - 1]) / price_data[i - 1] for i in range(1, len(price_data)) ]
        volatility = np.std(returns[-window:])

        features = [
            round(price_change, 6),
            round(sma, 6),
            round(rsi, 6),
            round(vol_change, 6),
            round(volatility, 6)
        ]

        log_event("Features extracted", meta={"features": features})
        return features
    except Exception as e:
        log_error("Feature extraction failed", meta={"error": str(e)})
        return [0.0] * 5
