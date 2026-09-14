"""Technical indicators — pure numpy, no TA-Lib dependency."""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np


def _to_array(values: Sequence[float]) -> np.ndarray:
    return np.asarray(list(values), dtype=float)


def sma(values: Sequence[float], window: int) -> Optional[float]:
    """Simple moving average of the last `window` observations."""
    arr = _to_array(values)
    if len(arr) < window:
        return None
    return float(np.mean(arr[-window:]))


def ema(values: Sequence[float], window: int) -> Optional[float]:
    """Exponential moving average of the last observation."""
    arr = _to_array(values)
    if len(arr) < window:
        return None
    alpha = 2.0 / (window + 1.0)
    e = arr[0]
    for v in arr[1:]:
        e = alpha * v + (1 - alpha) * e
    return float(e)


def rsi(values: Sequence[float], window: int = 14) -> Optional[float]:
    """Relative Strength Index (Wilder's smoothing)."""
    arr = _to_array(values)
    if len(arr) < window + 1:
        return None
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Wilder smoothing
    avg_gain = float(np.mean(gains[:window]))
    avg_loss = float(np.mean(losses[:window]))
    for i in range(window, len(gains)):
        avg_gain = (avg_gain * (window - 1) + gains[i]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i]) / window

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def macd(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Optional[dict]:
    """MACD line, signal line, histogram."""
    arr = _to_array(values)
    if len(arr) < slow + signal:
        return None

    def _ema_series(a: np.ndarray, span: int) -> np.ndarray:
        alpha = 2.0 / (span + 1.0)
        out = np.empty_like(a, dtype=float)
        out[0] = a[0]
        for i in range(1, len(a)):
            out[i] = alpha * a[i] + (1 - alpha) * out[i - 1]
        return out

    ema_fast = _ema_series(arr, fast)
    ema_slow = _ema_series(arr, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema_series(macd_line, signal)
    hist = macd_line - signal_line
    return {
        "macd": float(macd_line[-1]),
        "signal": float(signal_line[-1]),
        "histogram": float(hist[-1]),
    }


def bollinger_bands(
    values: Sequence[float], window: int = 20, num_std: float = 2.0
) -> Optional[dict]:
    arr = _to_array(values)
    if len(arr) < window:
        return None
    slice_ = arr[-window:]
    mid = float(np.mean(slice_))
    sd = float(np.std(slice_, ddof=0))
    return {
        "upper": mid + num_std * sd,
        "middle": mid,
        "lower": mid - num_std * sd,
        "bandwidth": (2 * num_std * sd) / mid if mid else 0.0,
    }


def zscore(values: Sequence[float], window: int = 20) -> Optional[float]:
    """Z-score of the latest observation vs. trailing window."""
    arr = _to_array(values)
    if len(arr) < window:
        return None
    slice_ = arr[-window:]
    mu = float(np.mean(slice_))
    sd = float(np.std(slice_, ddof=0))
    if sd == 0:
        return 0.0
    return float((arr[-1] - mu) / sd)
