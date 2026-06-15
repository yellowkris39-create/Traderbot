import pandas as pd
import pytest

from brokebyte.analysis.indicators import atr
from brokebyte.backtest.costs import CostModel
from brokebyte.backtest.engine import run_backtest
from brokebyte.guards.regime import classify_regime
from brokebyte.risk.limits import RiskLimits
from brokebyte.risk.sizing import size_position

LIMITS = RiskLimits()
COSTS = CostModel()


def make_trending_bars(direction="up", n_lead=50, extra=None):
    """`n_lead` bars stepping by $1/bar -> ATR(14)=2 and, once n_lead>=50,
    classify_regime sees Trend.UP (closes 51..) or Trend.DOWN (closes
    149..), mirroring tests/test_gate.py's make_trending_bars. `extra` is a
    list of explicit open/high/low/close dicts appended afterwards, used to
    control entry fills and stop/take-profit exits precisely."""
    if direction == "up":
        closes = [51.0 + i for i in range(n_lead)]
    else:
        closes = [149.0 - i for i in range(n_lead)]
    rows = [{"open": c, "high": c + 1.0, "low": c - 1.0, "close": c} for c in closes]
    rows.extend(extra or [])
    return pd.DataFrame(rows)


def test_no_trade_when_too_short():
    bars = make_trending_bars("up", n_lead=49)

    result = run_backtest(bars, "AAPL", LIMITS, COSTS)

    assert result.trades == []
    assert result.equity_curve == [100_000.0]


def test_long_entry_and_take_profit_exit():
    # Bar 50 enters at open=100. Bar 51's high spikes well past any
    # plausible take-profit, guaranteeing a take-profit exit.
    bars = make_trending_bars(
        "up",
        extra=[
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
            {"open": 101.0, "high": 200.0, "low": 99.0, "close": 105.0},
        ],
    )

    result = run_backtest(bars, "AAPL", LIMITS, COSTS, initial_equity=100_000.0)

    assert len(result.trades) == 1
    trade = result.trades[0]

    window = bars.iloc[:50]
    regime = classify_regime(window)
    entry_price = COSTS.apply_slippage(100.0, "buy")
    plan = size_position(
        symbol="AAPL",
        side="buy",
        entry_price=entry_price,
        atr=atr(window),
        equity=100_000.0,
        limits=LIMITS,
        size_multiplier=regime.size_multiplier,
    )
    assert plan is not None

    exit_price = COSTS.apply_slippage(plan.take_profit_price, "sell")
    exit_fees = COSTS.fees("sell", exit_price * plan.qty, plan.qty)
    expected_pnl = (exit_price - entry_price) * plan.qty - exit_fees

    assert trade.symbol == "AAPL"
    assert trade.side == "buy"
    assert trade.entry_index == 50
    assert trade.entry_price == pytest.approx(entry_price)
    assert trade.exit_index == 51
    assert trade.exit_reason == "take_profit"
    assert trade.exit_price == pytest.approx(exit_price)
    assert trade.qty == plan.qty
    assert trade.pnl == pytest.approx(expected_pnl)
    assert trade.r_multiple == pytest.approx(expected_pnl / plan.risk_amount)
    assert result.equity_curve[0] == 100_000.0
    assert result.equity_curve[1] == pytest.approx(100_000.0 + expected_pnl)


def test_short_entry_and_stop_loss_exit():
    # Bar 50 enters at open=100 (short). Bar 51's high spikes well past any
    # plausible stop, guaranteeing a stop-loss exit.
    bars = make_trending_bars(
        "down",
        extra=[
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
            {"open": 100.0, "high": 200.0, "low": 95.0, "close": 98.0},
        ],
    )

    result = run_backtest(bars, "AAPL", LIMITS, COSTS, initial_equity=100_000.0)

    assert len(result.trades) == 1
    trade = result.trades[0]

    window = bars.iloc[:50]
    regime = classify_regime(window)
    entry_price = COSTS.apply_slippage(100.0, "sell")
    plan = size_position(
        symbol="AAPL",
        side="sell",
        entry_price=entry_price,
        atr=atr(window),
        equity=100_000.0,
        limits=LIMITS,
        size_multiplier=regime.size_multiplier,
    )
    assert plan is not None

    exit_price = COSTS.apply_slippage(plan.stop_price, "buy")
    entry_fees = COSTS.fees("sell", entry_price * plan.qty, plan.qty)
    expected_pnl = (entry_price - exit_price) * plan.qty - entry_fees

    assert trade.side == "sell"
    assert trade.entry_index == 50
    assert trade.exit_index == 51
    assert trade.exit_reason == "stop"
    assert trade.exit_price == pytest.approx(exit_price)
    assert trade.qty == plan.qty
    assert trade.pnl == pytest.approx(expected_pnl)
    assert trade.pnl < 0
    assert result.equity_curve[1] == pytest.approx(100_000.0 + expected_pnl)


def test_long_entry_exits_at_end_of_data():
    # Bar 51 stays strictly between the stop and take-profit -> the
    # position is marked closed at bar 51's close ("end_of_data").
    bars = make_trending_bars(
        "up",
        extra=[
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
            {"open": 100.5, "high": 102.0, "low": 98.0, "close": 101.0},
        ],
    )

    result = run_backtest(bars, "AAPL", LIMITS, COSTS, initial_equity=100_000.0)

    assert len(result.trades) == 1
    trade = result.trades[0]

    exit_price = COSTS.apply_slippage(101.0, "sell")

    assert trade.exit_index == 51
    assert trade.exit_reason == "end_of_data"
    assert trade.exit_price == pytest.approx(exit_price)
