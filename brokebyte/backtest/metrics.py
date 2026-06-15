"""Success metrics for Track A backtests and Track B forward-paper soaks,
per SPEC.md Sec 5a: "define BEFORE the soak" -- Sortino preferred over
Sharpe, max drawdown + time-to-recover, profit factor + expectancy, win rate
(secondary), trade count, and regime coverage. Also encodes the §5a
promotion thresholds (PromotionThresholds/evaluate_promotion), written up
front before the Track B soak begins.

Sharpe/Sortino are computed over the per-trade return series
(pnl / equity-before-the-trade), against a target return of 0 -- there is
no risk-free-rate or annualization adjustment, since trades are not evenly
spaced in time. This makes the ratios most meaningful as *relative*
comparisons across walk-forward windows (Sec 5/Sec 6 build order step 5)
rather than as absolute figures.

compute_metrics takes a plain list of realized per-trade P&Ls so it can be
shared by Track A (BacktestTrade.pnl from brokebyte.backtest.engine) and
Track B (DecisionStore.closed_trade_pnls from brokebyte.memory.store)
without either track depending on the other's trade representation.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

import pandas as pd

from brokebyte.backtest.engine import MIN_LOOKBACK_BARS
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


def compute_metrics(pnls: list[float], initial_equity: float) -> PerformanceMetrics:
    if not pnls:
        return _empty_metrics()

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


@dataclass(frozen=True)
class PromotionThresholds:
    """§5a promotion thresholds for Track B's forward-paper soak, set in
    writing before the soak begins (SPEC.md Sec 5a / Decision rule) so they
    can't be loosened after seeing results.

    `min_trades` and `min_regime_types` gate whether there's enough data for
    the rest to be meaningful at all (trade count / regime coverage per
    Sec 5a) -- below these, evaluate_promotion reports "insufficient data"
    rather than pass/fail. The remaining thresholds are the actual bar:

    - `min_sortino` / `min_profit_factor` / `min_expectancy` = 0.0 / 1.0 /
      0.0 require a non-negative risk-adjusted edge, gross wins covering
      gross losses, and positive expectancy per trade -- i.e. "this
      strategy has ANY real edge over costs", the minimum meaningful bar
      for Rung 0. Given this strategy's fixed 1:2 risk:reward (stop = 2x
      ATR, take-profit = 4x ATR), a sub-50% win rate can already clear
      these, so they are not trivially satisfied.
    - `max_drawdown_pct` = 0.15 is tighter than Track A's 0.25 harness-sanity
      cap (SPEC.md Sec 5b) because this gates the strategy's real
      performance, not just harness mechanics.

    Raising these (e.g. requiring Sortino > 0.5-1.0) before promoting past
    Rung 0 is expected as the soak accumulates a track record -- but per
    Sec 5a that would be a new, written threshold for the *next* promotion
    decision, not a retroactive change to this one.
    """

    min_trades: int = 30
    min_regime_types: int = 2
    min_sortino: float = 0.0
    min_profit_factor: float = 1.0
    min_expectancy: float = 0.0
    max_drawdown_pct: float = 0.15


DEFAULT_THRESHOLDS = PromotionThresholds()


@dataclass(frozen=True)
class PromotionCheck:
    sufficient_data: bool  # enough trades + regime coverage for the rest to mean anything
    passed: bool  # sufficient_data and every threshold met
    failures: list[str]  # human-readable reasons; empty iff passed


def evaluate_promotion(
    metrics: PerformanceMetrics,
    regime_coverage: dict[Trend, int],
    thresholds: PromotionThresholds = DEFAULT_THRESHOLDS,
) -> PromotionCheck:
    """Check `metrics` (from compute_metrics) and `regime_coverage` (from
    regime_counts or DecisionStore.regime_coverage) against `thresholds`."""
    failures: list[str] = []

    regime_types_seen = sum(1 for count in regime_coverage.values() if count > 0)
    if metrics.trade_count < thresholds.min_trades:
        failures.append(f"trade_count {metrics.trade_count} < min_trades {thresholds.min_trades}")
    if regime_types_seen < thresholds.min_regime_types:
        failures.append(
            f"regime_types_seen {regime_types_seen} < min_regime_types {thresholds.min_regime_types}"
        )
    sufficient_data = not failures

    if sufficient_data:
        # sortino_ratio/profit_factor are None exactly when there are no
        # losing trades (zero downside deviation / zero gross loss) -- the
        # best case, not a failure.
        if metrics.sortino_ratio is not None and metrics.sortino_ratio < thresholds.min_sortino:
            failures.append(f"sortino_ratio {metrics.sortino_ratio} < min_sortino {thresholds.min_sortino}")
        if metrics.profit_factor is not None and metrics.profit_factor < thresholds.min_profit_factor:
            failures.append(
                f"profit_factor {metrics.profit_factor} < min_profit_factor {thresholds.min_profit_factor}"
            )
        if metrics.expectancy < thresholds.min_expectancy:
            failures.append(f"expectancy {metrics.expectancy} < min_expectancy {thresholds.min_expectancy}")
        if metrics.max_drawdown_pct > thresholds.max_drawdown_pct:
            failures.append(
                f"max_drawdown_pct {metrics.max_drawdown_pct} > max_drawdown_pct {thresholds.max_drawdown_pct}"
            )

    return PromotionCheck(sufficient_data=sufficient_data, passed=not failures, failures=failures)
