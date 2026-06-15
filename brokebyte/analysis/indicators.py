"""Minimal technical indicators shared by position sizing (risk), the
regime guard, and context fusion (Module 3). Operates on a pandas
DataFrame with at least `high`, `low`, `close` columns, oldest-first.
"""

from __future__ import annotations

import pandas as pd


def true_range(bars: pd.DataFrame) -> pd.Series:
    high, low, close = bars["high"], bars["low"], bars["close"]
    prev_close = close.shift(1)
    return pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr_series(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range, Wilder-style simple rolling mean (sufficient for
    sizing/regime purposes — not a full Wilder smoothing implementation)."""
    return true_range(bars).rolling(period).mean()


def atr(bars: pd.DataFrame, period: int = 14) -> float:
    """Latest ATR value. Raises if there isn't enough data."""
    value = atr_series(bars, period).iloc[-1]
    if pd.isna(value):
        raise ValueError(f"not enough bars ({len(bars)}) to compute ATR({period})")
    return float(value)


def sma(bars: pd.DataFrame, period: int, column: str = "close") -> float:
    """Latest simple moving average value. Raises if there isn't enough data."""
    value = bars[column].rolling(period).mean().iloc[-1]
    if pd.isna(value):
        raise ValueError(f"not enough bars ({len(bars)}) to compute SMA({period})")
    return float(value)


def support_resistance(bars: pd.DataFrame, lookback: int = 20) -> tuple[float, float]:
    """(support, resistance) = (lowest low, highest high) over the most
    recent `lookback` bars. Raises if there isn't enough data."""
    if len(bars) < lookback:
        raise ValueError(f"not enough bars ({len(bars)}) to compute support/resistance over {lookback}")
    window = bars.iloc[-lookback:]
    return float(window["low"].min()), float(window["high"].max())
