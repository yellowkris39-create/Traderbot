import pandas as pd

from brokebyte.guards.regime import MIN_SIZE_MULTIPLIER, Trend, classify_regime

FAST, SLOW, ATR_PERIOD = 3, 5, 3


def bars_from_closes(closes, high_offset=0.0, low_offset=0.0):
    return pd.DataFrame(
        {
            "high": [c + high_offset for c in closes],
            "low": [c - low_offset for c in closes],
            "close": closes,
        }
    )


def test_uptrend_low_vol_full_size():
    closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
    bars = bars_from_closes(closes)  # high == low == close -> small true ranges

    regime = classify_regime(bars, fast_period=FAST, slow_period=SLOW, atr_period=ATR_PERIOD)

    assert regime.trend == Trend.UP
    assert regime.high_volatility is False
    assert regime.size_multiplier == 1.0


def test_downtrend_low_vol_full_size():
    closes = [110, 109, 108, 107, 106, 105, 104, 103, 102, 101, 100]
    bars = bars_from_closes(closes)

    regime = classify_regime(bars, fast_period=FAST, slow_period=SLOW, atr_period=ATR_PERIOD)

    assert regime.trend == Trend.DOWN
    assert regime.size_multiplier == 1.0


def test_flat_series_is_choppy_and_halves_size():
    closes = [100.0] * 11
    bars = bars_from_closes(closes)

    regime = classify_regime(bars, fast_period=FAST, slow_period=SLOW, atr_period=ATR_PERIOD)

    assert regime.trend == Trend.CHOPPY
    assert regime.size_multiplier == 0.5


def test_high_volatility_halves_size():
    closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
    # Uptrend (so trend != CHOPPY) but with a wide daily high/low range
    # relative to price -> high ATR/close ratio.
    bars = bars_from_closes(closes, high_offset=10.0, low_offset=10.0)

    regime = classify_regime(bars, fast_period=FAST, slow_period=SLOW, atr_period=ATR_PERIOD)

    assert regime.trend == Trend.UP
    assert regime.high_volatility is True
    assert regime.size_multiplier == 0.5


def test_choppy_and_high_vol_compound_to_floor():
    closes = [100.0] * 11
    bars = bars_from_closes(closes, high_offset=10.0, low_offset=10.0)

    regime = classify_regime(bars, fast_period=FAST, slow_period=SLOW, atr_period=ATR_PERIOD)

    assert regime.trend == Trend.CHOPPY
    assert regime.high_volatility is True
    assert regime.size_multiplier == MIN_SIZE_MULTIPLIER


def test_insufficient_data_fails_safe():
    bars = bars_from_closes([100.0, 101.0])  # fewer rows than slow_period

    regime = classify_regime(bars, fast_period=FAST, slow_period=SLOW, atr_period=ATR_PERIOD)

    assert regime.trend == Trend.CHOPPY
    assert regime.high_volatility is True
    assert regime.size_multiplier == MIN_SIZE_MULTIPLIER
