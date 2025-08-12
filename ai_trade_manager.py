import os
import time
from datetime import datetime, timedelta

from logger import log_event, log_trade, log_error, log_heartbeat
from feature_engineering import extract_features
from learning_model import train_model, predict_action, record_trade

# ========= Env / Config =========
TRADE_MODE               = os.getenv("TRADE_MODE", "mock").lower()           # mock | live
SIGNAL_CONF_MIN          = float(os.getenv("SIGNAL_CONFIDENCE_MIN", "0.65"))
POSITION_SCALE_MAX       = int(os.getenv("POSITION_SCALE_MAX", "3"))
TRADE_USD_PER_ORDER      = float(os.getenv("TRADE_USD_PER_ORDER", "25"))
MAX_POSITION_USD         = float(os.getenv("MAX_POSITION_USD", "200"))

RETRAIN_INTERVAL_HOURS   = int(os.getenv("RETRAIN_INTERVAL_HOURS", "12"))
DRIFT_STD_THRESHOLD      = float(os.getenv("DRIFT_STD_THRESHOLD", "2.0"))     # reserved, if you compute drift later
MAX_DRAWDOWN_PCT         = float(os.getenv("STOP_AFTER_DRAWDOWN_PCT", "15"))
PAPER_ON_DRAWDOWN        = os.getenv("PAPER_TRADE_ON_DRAWDOWN", "true").lower() == "true"

HEARTBEAT_MINUTES        = int(os.getenv("LOG_INTERVAL_MINUTES", "15"))

# ========= Simple in-memory account state (replace with your wallet/exchange) =========
class AccountState:
    def __init__(self):
        self.cash_usd = 1000.0   # starting virtual cash for paper logic
        self.position_usd = 0.0  # total exposure
        self.equity_peak = self.cash_usd
        self.last_retrain_at = datetime.utcnow() - timedelta(hours=RETRAIN_INTERVAL_HOURS)
        self.last_heartbeat_at = datetime.utcnow() - timedelta(minutes=HEARTBEAT_MINUTES)

STATE = AccountState()

# ========= Helpers =========
def _now():
    return datetime.utcnow()

def _drawdown_pct(equity: float, peak: float) -> float:
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - equity) / peak * 100.0)

def _maybe_retrain():
    """Retrain on schedule; model uses history persisted by learning_model."""
    if _now() - STATE.last_retrain_at >= timedelta(hours=RETRAIN_INTERVAL_HOURS):
        log_event("Retraining model (scheduled)")
        try:
            train_model()
            STATE.last_retrain_at = _now()
            log_event("Retrain complete")
        except Exception as e:
            log_error("Retrain failed", meta={"error": str(e)})

def _maybe_heartbeat(symbol: str):
    if _now() - STATE.last_heartbeat_at >= timedelta(minutes=HEARTBEAT_MINUTES):
        STATE.last_heartbeat_at = _now()
        meta = {
            "mode": TRADE_MODE,
            "symbol": symbol,
            "cash_usd": round(STATE.cash_usd, 2),
            "position_usd": round(STATE.position_usd, 2),
        }
        log_heartbeat("alive", meta=meta)

def _risk_ok(size_usd: float) -> bool:
    return (STATE.position_usd + size_usd) <= MAX_POSITION_USD

def _apply_trade(side: str, price_usd: float, size_usd: float):
    """
    Paper-logic PnL tracking. Replace with your chain/exchange execution.
    """
    try:
        if side == "BUY":
            if not _risk_ok(size_usd):
                log_event("Skipped BUY: risk cap reached", level="WARNING",
                          meta={"requested_usd": size_usd, "position_usd": STATE.position_usd, "max": MAX_POSITION_USD})
                return
            STATE.position_usd += size_usd
            STATE.cash_usd -= size_usd
        elif side == "SELL":
            close_amt = min(size_usd, STATE.position_usd)
            STATE.position_usd -= close_amt
            STATE.cash_usd += close_amt
        equity = STATE.cash_usd + STATE.position_usd  # simplistic mark-to-cost
        STATE.equity_peak = max(STATE.equity_peak, equity)
        log_trade(side=side, symbol="SYMBOL", qty=size_usd/price_usd if price_usd > 0 else 0.0,
                  price=price_usd, meta={"mode": TRADE_MODE, "size_usd": size_usd, "equity": round(equity, 2)})
    except Exception as e:
        log_error("Trade application failed", meta={"error": str(e)})

def _maybe_stop_after_drawdown():
    equity = STATE.cash_usd + STATE.position_usd
    dd = _drawdown_pct(equity, STATE.equity_peak)
    if dd >= MAX_DRAWDOWN_PCT:
        if PAPER_ON_DRAWDOWN and TRADE_MODE != "mock":
            os.environ["TRADE_MODE"] = "mock"  # switch in-process
            log_event("Drawdown threshold hit — switching to PAPER (mock) mode",
                      level="WARNING", meta={"dd_pct": round(dd, 2)})
        else:
            log_event("Drawdown threshold hit — trading paused (no auto-switch configured)",
                      level="ERROR", meta={"dd_pct": round(dd, 2)})

# ========= Public Orchestrator =========
def decide_and_execute(symbol: str, price_series: list[float], volume_series: list[float]):
    """
    Feed market data -> features -> model -> (side, confidence) -> execute (paper/live)
    """
    try:
        _maybe_heartbeat(symbol)
        _maybe_retrain()

        features = extract_features(price_series, volume_series, window=14)
        # Minimal guard if features are zeros (not enough data yet)
        if all(v == 0.0 for v in features):
            log_event("Waiting for sufficient data", level="DEBUG", meta={"symbol": symbol})
            return

        side, conf = predict_action(features)

        # Scale position by confidence (linear up to POSITION_SCALE_MAX)
        scale = 1 + int((conf - SIGNAL_CONF_MIN) > 0) * min(
            POSITION_SCALE_MAX - 1, int((conf - SIGNAL_CONF_MIN) * POSITION_SCALE_MAX)
        )
        size_usd = TRADE_USD_PER_ORDER * max(1, scale)

        meta = {"symbol": symbol, "confidence": round(conf, 4), "size_usd": size_usd}
        if conf < SIGNAL_CONF_MIN:
            log_event("Confidence below threshold — no trade", level="INFO", meta=meta)
            record_trade(features, outcome=0)  # treat as no-trade/neutral outcome
            return

        # Execute (paper/live)
        if TRADE_MODE == "live":
            # TODO: Replace with your chain/exchange execution; ensure slippage & gas caps are enforced.
            log_event(f"Live execution placeholder: {side}", level="WARNING", meta=meta)
        else:
            # Paper execution
            last_price = price_series[-1] if price_series else 0.0
            _apply_trade(side, last_price, size_usd)

        # Record outcome seed (for online learning you’d update after you see PnL; here we log decision)
        record_trade(features, outcome=1 if side == "BUY" else 0)
        _maybe_stop_after_drawdown()

    except Exception as e:
        log_error("decide_and_execute failed", meta={"error": str(e)})
