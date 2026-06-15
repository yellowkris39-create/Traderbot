import math
import statistics

import pandas as pd
import pytest

from brokebyte.backtest.engine import BacktestTrade
from brokebyte.backtest.metrics import compute_metrics, regime_counts
from brokebyte.guards.regime import Trend


def make_trade(pnl, **overrides):
    defaults = dict(
        symbol="AAPL",
        side="buy",
        entry_index=0,
        entry_label=0,
        entry_price=100.0,
        exit_index=1,
        exit_label=1,
        exit_price=100.0,
        exit_reason="end_of_data",
        qty=1,
        pnl=pnl,
        r_multiple=0.0,
    )
    defaults.update(overrides)
    return BacktestTrade(**defaults)


def test_empty_trades():
    metrics = compute_metrics([], initial_equity=100_000.0)

    assert metrics.trade_count == 0
    assert metrics.win_rate == 0.0
    assert metrics.profit_factor is None
    assert metrics.expectancy == 0.0
    assert metrics.sharpe_ratio is None
    assert metrics.sortino_ratio is None
    assert metrics.max_drawdown_pct == 0.0
    assert metrics.max_drawdown_recovery_trades is None
    assert metrics.total_return_pct == 0.0


def test_all_winning_trades_have_no_drawdown_and_undefined_profit_factor():
    trades = [make_trade(100.0), make_trade(200.0), make_trade(50.0)]

    metrics = compute_metrics(trades, initial_equity=10_000.0)

    assert metrics.trade_count == 3
    assert metrics.win_rate == 1.0
    assert metrics.profit_factor is None  # no losses -> undefined
    assert metrics.expectancy == pytest.approx((100 + 200 + 50) / 3)
    assert metrics.max_drawdown_pct == 0.0
    assert metrics.max_drawdown_recovery_trades is None
    assert metrics.sortino_ratio is None  # no downside -> undefined
    assert metrics.total_return_pct == pytest.approx(350.0 / 10_000.0)


def test_mixed_trades_drawdown_and_recovery():
    pnls = [1000.0, -500.0, -1000.0, 2000.0, -200.0]
    trades = [make_trade(p) for p in pnls]
    initial_equity = 10_000.0

    metrics = compute_metrics(trades, initial_equity=initial_equity)

    equity_curve = [initial_equity]
    for p in pnls:
        equity_curve.append(equity_curve[-1] + p)
    assert equity_curve == [10_000, 11_000, 10_500, 9_500, 11_500, 11_300]

    assert metrics.trade_count == 5
    assert metrics.win_rate == pytest.approx(2 / 5)

    gross_win = 1000 + 2000
    gross_loss = 500 + 1000 + 200
    assert metrics.profit_factor == pytest.approx(gross_win / gross_loss)
    assert metrics.expectancy == pytest.approx(sum(pnls) / 5)

    # Peak of 11_000 (after trade 1) to trough of 9_500 (after trade 3);
    # recovers one trade later when equity reaches 11_500.
    assert metrics.max_drawdown_pct == pytest.approx(1_500 / 11_000)
    assert metrics.max_drawdown_recovery_trades == 1

    assert metrics.total_return_pct == pytest.approx(1_300 / 10_000)

    returns = [pnls[i] / equity_curve[i] for i in range(5)]
    expected_sharpe = statistics.mean(returns) / statistics.stdev(returns)
    assert metrics.sharpe_ratio == pytest.approx(expected_sharpe)

    downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in returns) / len(returns))
    expected_sortino = statistics.mean(returns) / downside
    assert metrics.sortino_ratio == pytest.approx(expected_sortino)


def test_single_trade_has_no_sharpe_ratio():
    metrics = compute_metrics([make_trade(100.0)], initial_equity=10_000.0)

    assert metrics.trade_count == 1
    assert metrics.sharpe_ratio is None  # stdev needs >= 2 returns


def test_drawdown_never_recovers():
    pnls = [1000.0, -2000.0]
    trades = [make_trade(p) for p in pnls]

    metrics = compute_metrics(trades, initial_equity=10_000.0)

    # equity curve: 10_000 -> 11_000 -> 9_000; never returns to 11_000.
    assert metrics.max_drawdown_pct == pytest.approx(2_000 / 11_000)
    assert metrics.max_drawdown_recovery_trades is None


# --- regime_counts -----------------------------------------------------------


def make_trending_bars(direction="up", n=60):
    if direction == "up":
        closes = [51.0 + i for i in range(n)]
    else:
        closes = [149.0 - i for i in range(n)]
    return pd.DataFrame({"high": [c + 1.0 for c in closes], "low": [c - 1.0 for c in closes], "close": closes})


def test_regime_counts_all_up():
    bars = make_trending_bars("up", n=60)

    counts = regime_counts(bars)

    assert counts[Trend.UP] == 60 - 49  # bars 49..59 inclusive
    assert counts[Trend.DOWN] == 0
    assert counts[Trend.CHOPPY] == 0


def test_regime_counts_too_short_is_choppy():
    bars = make_trending_bars("up", n=49)

    counts = regime_counts(bars)

    assert counts == {Trend.UP: 0, Trend.DOWN: 0, Trend.CHOPPY: 0}
