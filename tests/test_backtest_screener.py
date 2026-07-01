"""Tests for the screener walk-forward backtest simulator + metrics."""

from __future__ import annotations

import pandas as pd

from brokebyte.screener import backtest as bt


def _bars(rows):
    """rows: list of (open, high, low, close)."""
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"]).assign(
        volume=[1_000_000] * len(rows))


def test_simulate_hits_target():
    # entry 100, stop 95 (risk 5), target 110. Day 2 high reaches 111 -> target.
    bars = _bars([(100, 100, 100, 100),   # entry_idx bar
                  (100, 103, 99, 102),    # no hit
                  (103, 111, 102, 110)])  # target hit
    t = bt.simulate_trade(bars, 0, 100, 95, 110)
    assert t.exit_reason == "target" and t.r_multiple == 2.0


def test_simulate_hits_stop():
    bars = _bars([(100, 100, 100, 100),
                  (100, 101, 94, 95)])    # low 94 <= stop 95
    t = bt.simulate_trade(bars, 0, 100, 95, 110)
    assert t.exit_reason == "stop" and t.r_multiple == -1.0


def test_simulate_stop_before_target_when_both_touched():
    # bar touches both stop (94) and target (110): stop assumed first.
    bars = _bars([(100, 100, 100, 100),
                  (100, 111, 94, 100)])
    t = bt.simulate_trade(bars, 0, 100, 95, 110)
    assert t.exit_reason in ("stop", "trail_or_be_stop") and t.r_multiple <= 0


def test_simulate_time_stop():
    # never hits stop/target; exits at close of day 10.
    rows = [(100, 101, 99, 100)] + [(100, 101, 99, 100)] * 12
    t = bt.simulate_trade(_bars(rows), 0, 100, 90, 130, max_holding_days=10)
    assert t.exit_reason == "time_stop" and t.bars_held == 10


def test_breakeven_protects_after_1r():
    # rises to +1R (105) then falls back through entry -> break-even stop ~0R.
    bars = _bars([(100, 100, 100, 100),
                  (100, 106, 100, 105),   # close 105 = +1R -> stop moves to 100
                  (105, 105, 99, 100)])   # low 99 <= new stop 100 -> exit ~0R
    t = bt.simulate_trade(bars, 0, 100, 95, 130)
    assert t.r_multiple == 0.0 and t.exit_reason == "trail_or_be_stop"


def test_metrics_basic():
    trades = [
        bt.BacktestTrade("A", 0, 100, 95, 110, 3, 110, "target", 2.0, 3),
        bt.BacktestTrade("A", 4, 100, 95, 110, 6, 95, "stop", -1.0, 2),
    ]
    m = bt.compute_metrics(trades)
    assert m.trades == 2 and m.wins == 1 and m.win_rate == 0.5
    assert m.total_r == 1.0 and m.avg_r == 0.5
    assert m.best_r == 2.0 and m.worst_r == -1.0


def test_metrics_empty():
    m = bt.compute_metrics([])
    assert m.trades == 0 and m.win_rate == 0.0


def test_backtest_symbol_runs_and_returns_list():
    # long flat-ish series: should run end-to-end and return a (possibly empty) list
    import pandas as pd
    n = 260
    closes = [50 + 30 * i / (n - 1) for i in range(n)]
    df = pd.DataFrame({"open": closes, "high": [c + 0.5 for c in closes],
                       "low": [c - 0.5 for c in closes], "close": closes,
                       "volume": [1_000_000] * n})
    idx = pd.DataFrame({"open": [100] * n, "high": [100.1] * n,
                        "low": [99.9] * n, "close": [100] * n, "volume": [1] * n})
    trades = bt.backtest_symbol(df, idx, symbol="TEST")
    assert isinstance(trades, list)
    m = bt.compute_metrics(trades)
    assert m.trades == len(trades)


def test_partition_trades_by_entry_idx():
    trades = [
        bt.BacktestTrade("A", 5, 100, 95, 110, 8, 110, "target", 2.0, 3),
        bt.BacktestTrade("A", 50, 100, 95, 110, 55, 95, "stop", -1.0, 5),
    ]
    is_, oos = bt.partition_trades(trades, split_idx=20)
    assert len(is_) == 1 and is_[0].entry_idx == 5
    assert len(oos) == 1 and oos[0].entry_idx == 50


def test_target_rr_sets_target_distance():
    # higher target_rr => target further from entry => same risk, bigger win R
    bars = _bars([(100, 100, 100, 100),
                  (100, 109, 99, 105),
                  (105, 116, 104, 115)])   # high 116 reaches a 3R target (115) but not below
    t2 = bt.simulate_trade(bars, 0, 100, 95, 100 + 2 * 5)   # 2R target=110
    t3 = bt.simulate_trade(bars, 0, 100, 95, 100 + 3 * 5)   # 3R target=115
    assert t2.exit_reason == "target" and t2.r_multiple == 2.0
    assert t3.exit_reason == "target" and t3.r_multiple == 3.0


def test_metrics_avg_win_loss():
    trades = [
        bt.BacktestTrade("A", 0, 100, 95, 110, 3, 110, "target", 2.0, 3),
        bt.BacktestTrade("A", 4, 100, 95, 110, 6, 95, "stop", -1.0, 2),
        bt.BacktestTrade("A", 7, 100, 95, 110, 9, 100, "time_stop", 0.0, 2),
    ]
    m = bt.compute_metrics(trades)
    assert m.avg_win_r == 2.0           # only the +2R win
    assert m.avg_loss_r == -0.5         # mean of -1.0 and 0.0


def test_apply_slippage_reduces_r():
    # entry 100, stop 95 (risk 5). 0.1%/side => cost_R = 2*0.001*100/5 = 0.04R.
    t = bt.BacktestTrade("A", 0, 100, 95, 110, 3, 110, "target", 2.0, 3)
    adj = bt.apply_slippage([t], 0.001)[0]
    assert round(adj.r_multiple, 4) == round(2.0 - 0.04, 4)


def test_apply_slippage_zero_is_noop():
    t = bt.BacktestTrade("A", 0, 100, 95, 110, 3, 110, "target", 2.0, 3)
    assert bt.apply_slippage([t], 0.0)[0].r_multiple == 2.0
