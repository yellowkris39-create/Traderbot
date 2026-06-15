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
