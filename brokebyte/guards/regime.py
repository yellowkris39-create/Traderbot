"""Guard 9 — Regime Filter.

Classifies the recent regime as trending (up/down) or choppy, and as
high- or low-volatility, from daily price bars. Trend-following logic dies
in chop, so a choppy and/or high-volatility regime reduces the position
size multiplier (consumed by risk/sizing.py) rather than blocking outright.

If there isn't enough bar history to classify, fail safe to the most
conservative regime (choppy, high-vol, smallest size multiplier).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from brokebyte.analysis.indicators import atr, sma


class Trend(str, Enum):
    UP = "up"
    DOWN = "down"
    CHOPPY = "choppy"


MIN_SIZE_MULTIPLIER = 0.25


@dataclass(frozen=True)
class Regime:
    trend: Trend
    high_volatility: bool
    size_multiplier: float


def classify_regime(
    bars: pd.DataFrame,
    fast_period: int = 20,
    slow_period: int = 50,
    atr_period: int = 14,
    high_vol_atr_pct: float = 0.03,
    trend_band_pct: float = 0.002,
) -> Regime:
    try:
        fast = sma(bars, fast_period)
        slow = sma(bars, slow_period)
        atr_value = atr(bars, atr_period)
    except ValueError:
        return Regime(trend=Trend.CHOPPY, high_volatility=True, size_multiplier=MIN_SIZE_MULTIPLIER)

    last_close = float(bars["close"].iloc[-1])

    spread = (fast - slow) / slow if slow != 0 else 0.0
    if spread > trend_band_pct:
        trend = Trend.UP
    elif spread < -trend_band_pct:
        trend = Trend.DOWN
    else:
        trend = Trend.CHOPPY

    high_volatility = (atr_value / last_close) > high_vol_atr_pct if last_close > 0 else True

    multiplier = 1.0
    if trend == Trend.CHOPPY:
        multiplier *= 0.5
    if high_volatility:
        multiplier *= 0.5

    return Regime(trend=trend, high_volatility=high_volatility, size_multiplier=max(multiplier, MIN_SIZE_MULTIPLIER))
