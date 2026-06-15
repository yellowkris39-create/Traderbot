"""Success metrics for Track A backtests and (later) Track B forward-paper
soaks, per SPEC.md Sec 5a: "define BEFORE the soak" -- Sortino preferred
over Sharpe, max drawdown + time-to-recover, profit factor + expectancy,
win rate (secondary), trade count, and regime coverage.

Sharpe/Sortino are computed over the per-trade return series
(pnl / equity-before-the-trade), against a target return of 0 -- there is
no risk-free-rate or annualization adjustment, since trades are not evenly
spaced in time. This makes the ratios most meaningful as *relative*
comparisons across walk-forward windows (Sec 5/Sec 6 build order step 5)
rather than as absolute figures.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

import pandas as pd

from brokebyte.backtest.engine import BacktestTrade, MIN_LOOKBACK_BARS
from brokebyte.guards.regime import Trend, classify_regime


@dataclass(frozen=True)
class PerformanceMetrics:
    trade_count: int
    win_rate: float
    profit_factor: float | None  # gross win / gross loss; None if no losing trades
    expectancy: float  # mean pnl per trade
    sharpe_ratio: float | None  # mean(returns) / stdev(returns); None if <2 trades or stdev==0
    sortino_ratio: float | None  # mean(returns) / downside deviation; None if downside deviation==0
    max_drawdown_pct: float  # fraction of the peak equity, e.g. 0.05 == 5%
    max_drawdown_recovery_trades: int | None  # trades from trough back to prior peak; None if never recovered
    total_return_pct: float  # (final equity - initial equity) / initial equity


def _empty_metrics() -> PerformanceMetrics:
    return PerformanceMetrics(
        trade_count=0,
        win_rate=0.0,
        profit_factor=None,
        expectancy=0.0,
        sharpe_ratio=None,
        sortino_ratio=None,
        max_drawdown_pct=0.0,
        max_drawdown_recovery_trades=None,
        total_return_pct=0.0,
    )


def _drawdown(equity_curve: list[float]) -> tuple[float, int | None]:
    """Return (max_drawdown_pct, recovery_trades) for an equity curve that
    starts with the pre-trade equity. recovery_trades counts steps from the
    drawdown's trough until equity first returns to the prior peak; None if
    that never happens (or there is no drawdown)."""
    peak = equity_curve[0]
    peak_idx = 0
    max_dd = 0.0
    trough_idx = 0
    dd_peak_idx = 0

    for idx, equity in enumerate(equity_curve):
        if equity > peak:
            peak = equity
            peak_idx = idx
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            trough_idx = idx
            dd_peak_idx = peak_idx

    if max_dd == 0.0:
        return 0.0, None

    target = equity_curve[dd_peak_idx]
    for idx in range(trough_idx + 1, len(equity_curve)):
        if equity_curve[idx] >= target:
            return max_dd, idx - trough_idx

    return max_dd, None


def compute_metrics(trades: list[BacktestTrade], initial_equity: float) -> PerformanceMetrics:
    if not trades:
        return _empty_metrics()

    pnls = [t.pnl for t in trades]
    trade_count = len(pnls)

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / trade_count

    gross_win = sum(wins)
    gross_loss = -sum(losses)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None

    expectancy = sum(pnls) / trade_count

    equity_curve = [initial_equity]
    for pnl in pnls:
        equity_curve.append(equity_curve[-1] + pnl)

    returns = [pnl / equity_curve[idx] for idx, pnl in enumerate(pnls)]

    sharpe_ratio = None
    if len(returns) >= 2:
        mean_return = statistics.mean(returns)
        stdev_return = statistics.stdev(returns)
        if stdev_return > 0:
            sharpe_ratio = mean_return / stdev_return

    sortino_ratio = None
    downside_deviation = math.sqrt(sum(min(r, 0.0) ** 2 for r in returns) / len(returns))
    if downside_deviation > 0:
        sortino_ratio = statistics.mean(returns) / downside_deviation

    max_drawdown_pct, recovery_trades = _drawdown(equity_curve)
    total_return_pct = (equity_curve[-1] - initial_equity) / initial_equity

    return PerformanceMetrics(
        trade_count=trade_count,
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        max_drawdown_pct=max_drawdown_pct,
        max_drawdown_recovery_trades=recovery_trades,
        total_return_pct=total_return_pct,
    )


def regime_counts(bars: pd.DataFrame) -> dict[Trend, int]:
    """Tally how often each Trend is classified while walking `bars`
    forward with the same no-lookahead windowing run_backtest uses, to
    report "regime coverage" per SPEC.md Sec 5a."""
    counts = {Trend.UP: 0, Trend.DOWN: 0, Trend.CHOPPY: 0}
    for i in range(MIN_LOOKBACK_BARS - 1, len(bars)):
        regime = classify_regime(bars.iloc[: i + 1])
        counts[regime.trend] += 1
    return counts
