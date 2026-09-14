"""Interest-rate specific indicators."""

from __future__ import annotations

from typing import Optional, Sequence


def term_spread(long_yield: float, short_yield: float) -> float:
    """Simple term spread (long - short), e.g. 10Y - 2Y."""
    return float(long_yield) - float(short_yield)


def yield_changes(current: dict[str, float], previous: dict[str, float]) -> dict[str, float]:
    """Compute bp changes per tenor between two yield snapshots."""
    changes: dict[str, float] = {}
    for tenor, cur in current.items():
        prev = previous.get(tenor)
        if prev is None:
            continue
        changes[tenor] = round((cur - prev) * 100.0, 2)   # in basis points
    return changes


def curve_shape(spreads: dict[str, float]) -> str:
    """Classify the yield curve shape from key spreads."""
    s_10_2 = spreads.get("10Y_2Y")
    s_10_3m = spreads.get("10Y_3M")
    if s_10_2 is None:
        return "unknown"
    if s_10_2 < 0 and (s_10_3m is not None and s_10_3m < 0):
        return "inverted"
    if s_10_2 < 0:
        return "partially_inverted"
    if s_10_2 < 0.25:
        return "flat"
    return "normal"
