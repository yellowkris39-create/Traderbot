import pandas as pd
import pytest

from brokebyte.analysis import indicators


def _bars(highs, lows, closes):
    return pd.DataFrame({"high": highs, "low": lows, "close": closes})


def test_atr_constant_range_matches_true_range():
    # Every bar has high-low = 2, no gaps -> ATR should converge to 2.
    n = 20
    closes = [100.0] * n
    highs = [101.0] * n
    lows = [99.0] * n
    bars = _bars(highs, lows, closes)

    value = indicators.atr(bars, period=14)

    assert value == pytest.approx(2.0)


def test_atr_raises_with_insufficient_data():
    bars = _bars([101, 102], [99, 100], [100, 101])

    with pytest.raises(ValueError):
        indicators.atr(bars, period=14)


def test_sma_simple_average():
    closes = list(range(1, 11))  # 1..10
    bars = _bars(closes, closes, closes)

    value = indicators.sma(bars, period=10)

    assert value == pytest.approx(5.5)


def test_sma_raises_with_insufficient_data():
    bars = _bars([1, 2, 3], [1, 2, 3], [1, 2, 3])

    with pytest.raises(ValueError):
        indicators.sma(bars, period=10)


def test_support_resistance_uses_lowest_low_and_highest_high():
    highs = [101, 105, 102, 110, 103]
    lows = [99, 100, 98, 104, 101]
    closes = [100, 103, 100, 107, 102]
    bars = _bars(highs, lows, closes)

    support, resistance = indicators.support_resistance(bars, lookback=5)

    assert support == 98
    assert resistance == 110


def test_support_resistance_only_considers_lookback_window():
    # First bar has the widest range but falls outside a 3-bar lookback.
    highs = [200, 101, 102, 103]
    lows = [1, 99, 98, 100]
    closes = [100, 100, 100, 100]
    bars = _bars(highs, lows, closes)

    support, resistance = indicators.support_resistance(bars, lookback=3)

    assert support == 98
    assert resistance == 103


def test_support_resistance_raises_with_insufficient_data():
    bars = _bars([101, 102], [99, 100], [100, 101])

    with pytest.raises(ValueError):
        indicators.support_resistance(bars, lookback=3)
