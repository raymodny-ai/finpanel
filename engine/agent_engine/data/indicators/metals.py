"""Metals-specific indicators."""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np


def gold_silver_ratio(gold_prices: Sequence[float], silver_prices: Sequence[float]) -> Optional[float]:
    """Latest gold/silver ratio (both should be spot USD/oz)."""
    if not gold_prices or not silver_prices:
        return None
    g = float(gold_prices[-1])
    s = float(silver_prices[-1])
    if s == 0:
        return None
    return g / s


def real_yield_from_tips(tips_10y: float) -> float:
    """TIPS 10Y is already the real yield — identity helper for clarity."""
    return float(tips_10y)


def real_rate_regression(
    gold_prices: Sequence[float],
    real_rates: Sequence[float],
    window: int = 60,
) -> Optional[dict]:
    """Rolling regression of gold vs. 10Y real yield.

    Historically the correlation is negative (real rates up -> gold down).
    Returns slope, correlation, R^2 for the last `window` observations.
    """
    g = np.asarray(list(gold_prices), dtype=float)
    r = np.asarray(list(real_rates), dtype=float)
    n = min(len(g), len(r), window)
    if n < 10:
        return None

    g = g[-n:]
    r = r[-n:]

    # Pearson correlation
    if np.std(g) == 0 or np.std(r) == 0:
        return {"slope": 0.0, "correlation": 0.0, "r_squared": 0.0, "n": n}

    corr = float(np.corrcoef(g, r)[0, 1])
    slope, intercept = np.polyfit(r, g, 1)

    # R^2
    y_hat = slope * r + intercept
    ss_res = float(np.sum((g - y_hat) ** 2))
    ss_tot = float(np.sum((g - np.mean(g)) ** 2))
    r_sq = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return {
        "slope": float(slope),
        "intercept": float(intercept),
        "correlation": corr,
        "r_squared": float(r_sq),
        "n": n,
    }


def mining_cost_curve_placeholder() -> dict:
    """Placeholder for AISC (all-in sustaining cost) curve by producer.

    Real data would come from company filings; MVP returns a static reference.
    """
    return {
        "note": "AISC reference values (2025 industry aggregate)",
        "curve": [
            {"percentile": 25, "aisc_usd_oz": 950},
            {"percentile": 50, "aisc_usd_oz": 1180},
            {"percentile": 75, "aisc_usd_oz": 1420},
            {"percentile": 90, "aisc_usd_oz": 1620},
        ],
    }


def seasonality_factor() -> dict:
    """Historical monthly seasonality for gold (approximate industry averages)."""
    return {
        "note": "Average monthly gold return, 1972-2024 (illustrative)",
        "monthly_avg_pct": {
            "1": 2.6, "2": 1.5, "3": -0.5, "4": 0.8,
            "5": -0.7, "6": 0.4, "7": 1.1, "8": 1.4,
            "9": -1.5, "10": 0.6, "11": 1.4, "12": 0.5,
        },
        "strongest_months": ["1", "2", "8", "11"],
        "weakest_months": ["3", "5", "9"],
    }
