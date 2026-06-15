"""Walk-forward harness for Track A (SPEC.md Sec 5 / Sec 6 build order step 5).

Splits `bars` into `n_windows` sequential, non-overlapping windows and runs
the *same fixed* run_backtest config on each. This demonstrates whether the
mechanical strategy behaves consistently out-of-sample across different
historical periods/regimes -- it is explicitly NOT a parameter search (the
config does not vary across windows), per SPEC.md Sec 5's data-snooping
warning ("don't try 100 configs and keep the prettiest").

Each window re-applies run_backtest's own MIN_LOOKBACK_BARS warm-up, so a
window's first ~50 bars cannot generate trades. Windows are not required to
be equal-sized: the last window absorbs any remainder bars.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from brokebyte.backtest.costs import CostModel
from brokebyte.backtest.engine import BacktestResult, bar_label, run_backtest
from brokebyte.backtest.metrics import PerformanceMetrics, compute_metrics, regime_counts
from brokebyte.guards.regime import Trend
from brokebyte.risk.limits import RiskLimits


@dataclass(frozen=True)
class WalkForwardWindow:
    start_index: int
    end_index: int  # exclusive, relative to the original `bars`
    start_label: object | None
    end_label: object | None
    result: BacktestResult
    metrics: PerformanceMetrics
    regime_counts: dict[Trend, int]


def _window_label(bars: pd.DataFrame, i: int) -> object | None:
    if 0 <= i < len(bars):
        return bar_label(bars, i)
    return None


def run_walkforward(
    bars: pd.DataFrame,
    symbol: str,
    limits: RiskLimits,
    cost_model: CostModel,
    n_windows: int,
    initial_equity: float = 100_000.0,
) -> list[WalkForwardWindow]:
    if n_windows < 1:
        raise ValueError(f"n_windows must be >= 1, got {n_windows}")

    n = len(bars)
    window_size = n // n_windows

    windows: list[WalkForwardWindow] = []
    for w in range(n_windows):
        start = w * window_size
        end = n if w == n_windows - 1 else start + window_size
        window_bars = bars.iloc[start:end].reset_index(drop=True)

        result = run_backtest(window_bars, symbol, limits, cost_model, initial_equity)
        metrics = compute_metrics(result.trades, initial_equity)
        counts = regime_counts(window_bars)

        windows.append(
            WalkForwardWindow(
                start_index=start,
                end_index=end,
                start_label=_window_label(bars, start),
                end_label=_window_label(bars, end - 1),
                result=result,
                metrics=metrics,
                regime_counts=counts,
            )
        )

    return windows
