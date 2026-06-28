"""Extended technical indicators for the screener track (Strategy v2).

Adds EMA, RSI(14), average-volume, relative-strength, and the three
candlestick reversal patterns the swing-trade ruleset needs. These are
intentionally separate from `indicators.py` (SMA/ATR used by the news bot's
risk gate) so the news-bot risk path is untouched.

All functions operate on a pandas DataFrame, oldest row first, with at least
`open`, `high`, `low`, `close` columns (and `volume` where noted). They are
pure (no I/O) so they unit-test without a broker or data provider.
"""

from __future__ import annotations

import pandas as pd


# --------------------------------------------------------------------------
# Moving averages / oscillators
# --------------------------------------------------------------------------

def ema(bars: pd.DataFrame, period: int, column: str = "close") -> float:
    """Latest Exponential Moving Average value. Raises if too few bars."""
    if len(bars) < period:
        raise ValueError(f"not enough bars ({len(bars)}) to compute EMA({period})")
    return float(bars[column].ewm(span=period, adjust=False).mean().iloc[-1])


def rsi_series(bars: pd.DataFrame, period: int = 14, column: str = "close") -> pd.Series:
    """Wilder's RSI as a Series. NaN for the warm-up window."""
    delta = bars[column].diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    # Wilder smoothing == EWM with alpha = 1/period.
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    # When avg_loss is 0 the ratio is inf -> RSI 100; pandas already yields 100.
    return rsi


def rsi(bars: pd.DataFrame, period: int = 14, column: str = "close") -> float:
    """Latest RSI(period) value. Raises if there isn't enough data."""
    value = rsi_series(bars, period, column).iloc[-1]
    if pd.isna(value):
        raise ValueError(f"not enough bars ({len(bars)}) to compute RSI({period})")
    return float(value)


# --------------------------------------------------------------------------
# Volume
# --------------------------------------------------------------------------

def average_volume(bars: pd.DataFrame, period: int = 10) -> float:
    """Latest rolling mean of `volume` over `period` bars."""
    if "volume" not in bars.columns:
        raise ValueError("bars has no 'volume' column")
    value = bars["volume"].rolling(period).mean().iloc[-1]
    if pd.isna(value):
        raise ValueError(f"not enough bars ({len(bars)}) for average_volume({period})")
    return float(value)


def volume_surge(bars: pd.DataFrame, period: int = 10, threshold: float = 1.20) -> bool:
    """True if the latest bar's volume is >= `threshold` x the trailing
    `period`-bar average (excluding the latest bar)."""
    if "volume" not in bars.columns:
        raise ValueError("bars has no 'volume' column")
    if len(bars) < period + 1:
        raise ValueError(f"not enough bars ({len(bars)}) for volume_surge({period})")
    latest = float(bars["volume"].iloc[-1])
    trailing_avg = float(bars["volume"].iloc[-(period + 1):-1].mean())
    if trailing_avg <= 0:
        return False
    return latest >= threshold * trailing_avg


# --------------------------------------------------------------------------
# Relative strength vs an index
# --------------------------------------------------------------------------

def relative_strength(
    stock_bars: pd.DataFrame, index_bars: pd.DataFrame, lookback: int = 63
) -> float:
    """Stock's percentage return minus the index's percentage return over the
    last `lookback` bars (~63 trading days ≈ 3 months). Positive => the stock
    outperformed the index. Raises if either frame is too short."""
    if len(stock_bars) <= lookback or len(index_bars) <= lookback:
        raise ValueError("not enough bars to compute relative_strength")

    def _ret(bars: pd.DataFrame) -> float:
        start = float(bars["close"].iloc[-(lookback + 1)])
        end = float(bars["close"].iloc[-1])
        if start <= 0:
            raise ValueError("non-positive start price in relative_strength")
        return (end - start) / start

    return _ret(stock_bars) - _ret(index_bars)


def outperforms(
    stock_bars: pd.DataFrame, index_bars: pd.DataFrame, lookback: int = 63
) -> bool:
    """True if the stock outperformed the index over `lookback` bars."""
    return relative_strength(stock_bars, index_bars, lookback) > 0.0


# --------------------------------------------------------------------------
# Candlestick reversal patterns (evaluated on the most recent bar)
# --------------------------------------------------------------------------

def _body(o: float, c: float) -> float:
    return abs(c - o)


def is_hammer(bars: pd.DataFrame, lower_wick_ratio: float = 2.0) -> bool:
    """Hammer on the latest bar: long lower wick (>= ratio x body), small
    upper wick, body in the upper half of the range."""
    o, h, l, c = (float(bars[k].iloc[-1]) for k in ("open", "high", "low", "close"))
    body = _body(o, c)
    if body == 0:
        return False
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    return (
        lower_wick >= lower_wick_ratio * body
        and upper_wick <= body
        and (h - l) > 0
    )


def is_bullish_engulfing(bars: pd.DataFrame) -> bool:
    """Bullish engulfing: previous bar down, latest bar up, latest real body
    fully engulfs the previous real body."""
    if len(bars) < 2:
        return False
    po, pc = float(bars["open"].iloc[-2]), float(bars["close"].iloc[-2])
    o, c = float(bars["open"].iloc[-1]), float(bars["close"].iloc[-1])
    prev_down = pc < po
    curr_up = c > o
    engulfs = c >= max(po, pc) and o <= min(po, pc)
    return prev_down and curr_up and engulfs


def is_morning_star(bars: pd.DataFrame, small_body_ratio: float = 0.5) -> bool:
    """Three-bar morning star: bar -3 down with a large body, bar -2 a small
    body (the 'star'), bar -1 up closing above the midpoint of bar -3's body."""
    if len(bars) < 3:
        return False
    o3, c3 = float(bars["open"].iloc[-3]), float(bars["close"].iloc[-3])
    o2, c2 = float(bars["open"].iloc[-2]), float(bars["close"].iloc[-2])
    o1, c1 = float(bars["open"].iloc[-1]), float(bars["close"].iloc[-1])
    body3 = _body(o3, c3)
    body2 = _body(o2, c2)
    if body3 == 0:
        return False
    first_down = c3 < o3
    star_small = body2 <= small_body_ratio * body3
    third_up = c1 > o1
    closes_into_first = c1 > (o3 + c3) / 2
    return first_down and star_small and third_up and closes_into_first


def bullish_reversal(bars: pd.DataFrame) -> bool:
    """True if the latest bar(s) form any of the accepted bullish reversal
    patterns (hammer, bullish engulfing, or morning star)."""
    return is_hammer(bars) or is_bullish_engulfing(bars) or is_morning_star(bars)
