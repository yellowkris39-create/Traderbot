import pandas as pd
import pytest

from brokebyte.backtest.costs import CostModel
from brokebyte.backtest.metrics import compute_metrics, regime_counts
from brokebyte.backtest.walkforward import run_walkforward
from brokebyte.guards.regime import Trend
from brokebyte.risk.limits import RiskLimits

LIMITS = RiskLimits()
COSTS = CostModel()


def make_uptrend_bars(n):
    closes = [51.0 + i for i in range(n)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
        }
    )


def test_rejects_invalid_window_count():
    bars = make_uptrend_bars(150)

    with pytest.raises(ValueError):
        run_walkforward(bars, "AAPL", LIMITS, COSTS, n_windows=0)


def test_splits_into_equal_windows_with_no_trades_in_minimal_windows():
    # Exactly MIN_LOOKBACK_BARS (50) bars per window -> each window's loop
    # never runs (i < n - 1 is 49 < 49 == False), so no trades anywhere,
    # and regime_counts makes exactly one classify_regime call per window.
    bars = make_uptrend_bars(150)

    windows = run_walkforward(bars, "AAPL", LIMITS, COSTS, n_windows=3)

    assert len(windows) == 3
    expected_bounds = [(0, 50), (50, 100), (100, 150)]
    for window, (start, end) in zip(windows, expected_bounds):
        assert (window.start_index, window.end_index) == (start, end)
        assert window.start_label == start
        assert window.end_label == end - 1
        assert window.result.trades == []
        assert window.result.equity_curve == [100_000.0]
        assert window.regime_counts == {Trend.UP: 1, Trend.DOWN: 0, Trend.CHOPPY: 0}


def test_last_window_absorbs_remainder():
    bars = make_uptrend_bars(170)

    windows = run_walkforward(bars, "AAPL", LIMITS, COSTS, n_windows=3)

    assert [(w.start_index, w.end_index) for w in windows] == [(0, 56), (56, 112), (112, 170)]
    assert windows[0].start_index == 0
    assert windows[-1].end_index == 170
    for a, b in zip(windows, windows[1:]):
        assert a.end_index == b.start_index


def test_window_metrics_and_regime_counts_match_recomputation():
    bars = make_uptrend_bars(170)

    windows = run_walkforward(bars, "AAPL", LIMITS, COSTS, n_windows=3)

    for window in windows:
        window_bars = bars.iloc[window.start_index : window.end_index].reset_index(drop=True)
        assert window.metrics == compute_metrics(window.result.trades, 100_000.0)
        assert window.regime_counts == regime_counts(window_bars)
