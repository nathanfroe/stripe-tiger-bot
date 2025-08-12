import os
import json
import random
from datetime import datetime
import numpy as np
from sklearn.linear_model import SGDClassifier
from sklearn.preprocessing import StandardScaler
from logger import log_event, log_error

# ====== Config ======
MODEL_FILE = os.getenv("MODEL_FILE", "model_state.json")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.65"))
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "500"))

# ====== State ======
_scaler = StandardScaler()
_model = SGDClassifier(loss="log_loss", max_iter=1000, tol=1e-3)
_history = []

# ====== Persistence ======
def _save_state():
    try:
        state = {
            "scaler_mean": _scaler.mean_.tolist(),
            "scaler_scale": _scaler.scale_.tolist(),
            "model_coef": _model.coef_.tolist(),
            "model_intercept": _model.intercept_.tolist(),
            "history": _history,
        }
        with open(MODEL_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
        log_event("Model state saved", meta={"file": MODEL_FILE})
    except Exception as e:
        log_error("Failed to save model state", meta={"error": str(e)})

def _load_state():
    global _scaler, _model, _history
    try:
        if os.path.exists(MODEL_FILE):
            with open(MODEL_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            _scaler.mean_ = np.array(state["scaler_mean"])
            _scaler.scale_ = np.array(state["scaler_scale"])
            _model.coef_ = np.array(state["model_coef"])
            _model.intercept_ = np.array(state["model_intercept"])
            _history = state.get("history", [])
            log_event("Model state loaded", meta={"file": MODEL_FILE})
        else:
            log_event("No saved model found; starting fresh")
    except Exception as e:
        log_error("Failed to load model state", meta={"error": str(e)})

# ====== Core Functions ======
def record_trade(features: list[float], outcome: int):
    """
    Store historical trade data for learning.
    :param features: numeric indicators
    :param outcome: 1 = profit, 0 = loss
    """
    global _history
    ts = datetime.utcnow().isoformat()
    _history.append({"ts": ts, "features": features, "outcome": outcome})
    if len(_history) > MAX_HISTORY:
        _history.pop(0)
    log_event("Trade outcome recorded", meta={"outcome": outcome})
    _save_state()

def train_model():
    """
    Train model on stored trade history.
    """
    if not _history:
        log_event("No trade history to train on", level="WARNING")
        return

    X = np.array([h["features"] for h in _history])
    y = np.array([h["outcome"] for h in _history])

    try:
        X_scaled = _scaler.fit_transform(X)
        _model.partial_fit(X_scaled, y, classes=[0, 1
