"""Tests for the bars + signal cache fast-iteration path (offline)."""

from __future__ import annotations

import pandas as pd

from brokebyte.screener import btcache, backtest as bt


def _series(n=260, start=50.0, end=80.0):
    closes = [start + (end - start) * i / (n - 1) for i in range(n)]
    return pd.DataFrame({"open": closes, "high": [c + 0.5 for c in closes],
                         "low": [c - 0.5 for c in closes], "close": closes,
                         "volume": [1_000_000] * n})


def _qual_bars():
    # reuse the known-qualifying construction so signals are non-empty
    from tests.test_screener_pipeline import _qualifying_bars  # type: ignore
    return _qualifying_bars()


def test_safe_filename():
    assert btcache._safe("^FTSE") == "_FTSE"
    assert btcache._safe("BT-A.L") == "BT-A.L"


def test_bars_roundtrip(tmp_path):
    btcache.save_bars(tmp_path, "AAPL", _series())
    assert len(btcache.load_cached_bars(tmp_path, "AAPL")) == 260
    assert btcache.load_cached_bars(tmp_path, "MISSING") is None


def test_fast_path_matches_backtest_symbol():
    # The signal-once + simulate path must equal the original walk-forward.
    bars = _series(n=300, start=40, end=120)
    idx = _series(n=300, start=100, end=100)  # flat index
    direct = bt.backtest_symbol(bars, idx, symbol="X", target_rr=2.0)
    sigs = btcache.find_signals(bars, idx, apply_regime=False)
    fast = btcache.simulate_from_signals(bars, sigs, symbol="X", target_rr=2.0)
    assert [t.exit_idx for t in direct] == [t.exit_idx for t in fast]
    assert [round(t.r_multiple, 6) for t in direct] == [round(t.r_multiple, 6) for t in fast]


def test_fast_path_matches_with_different_exit_params():
    bars = _series(n=300, start=40, end=120)
    idx = _series(n=300, start=100, end=100)
    sigs = btcache.find_signals(bars, idx, apply_regime=False)
    for params in ({"target_rr": 3.0}, {"breakeven_at_r": 99, "trail_at_r": 99}, {"max_holding_days": 20}):
        direct = bt.backtest_symbol(bars, idx, symbol="X", **params)
        fast = btcache.simulate_from_signals(bars, sigs, symbol="X", **params)
        assert [t.exit_idx for t in direct] == [t.exit_idx for t in fast]


def test_signal_cache_roundtrip_and_sweep(tmp_path):
    bars = _series(n=300, start=40, end=120)
    idx = _series(n=300, start=100, end=100)
    btcache.save_bars(tmp_path, "AAPL", bars)
    btcache.save_bars(tmp_path, "SPY", idx)
    assert btcache.build_signal_cache(["AAPL"], tmp_path) == 1
    assert btcache.load_signals(tmp_path, "AAPL") is not None
    out = btcache.sweep(["AAPL"], tmp_path)
    assert out.count("\n") == len(btcache.DEFAULT_GRID)  # header + row per variant
    assert "baseline" in out


def test_named_grids_present():
    assert {"default", "hold", "round2", "round3"} <= set(btcache.GRIDS)
    assert any(v.get("max_holding_days") == 20 for v in btcache.HOLD_ROBUSTNESS_GRID)


def test_sweep_with_slippage_runs(tmp_path):
    bars = _series(n=300, start=40, end=120)
    idx = _series(n=300, start=100, end=100)
    btcache.save_bars(tmp_path, "AAPL", bars)
    btcache.save_bars(tmp_path, "SPY", idx)
    btcache.build_signal_cache(["AAPL"], tmp_path)
    out = btcache.sweep(["AAPL"], tmp_path, grid=btcache.HOLD_ROBUSTNESS_GRID, slippage_pct=0.001)
    assert "slippage" in out and out.count("\n") == len(btcache.HOLD_ROBUSTNESS_GRID)


def test_round3_grid_and_candidate():
    assert "round3" in btcache.GRIDS
    assert btcache.CANDIDATE == {"max_holding_days": 20, "target_rr": 2.0}


def test_walk_forward_reports_k_folds(tmp_path):
    bars = _series(n=320, start=40, end=130)
    idx = _series(n=320, start=100, end=100)
    btcache.save_bars(tmp_path, "AAPL", bars)
    btcache.save_bars(tmp_path, "SPY", idx)
    btcache.build_signal_cache(["AAPL"], tmp_path)
    out = btcache.walk_forward(["AAPL"], tmp_path, k=5, **btcache.CANDIDATE)
    assert out.count("fold ") == 5


def test_regime_gate_suppresses_signals_below_200sma():
    import pandas as pd
    # rising stock; index that is DOWN (below its own 200SMA) -> regime blocks entries
    n = 300
    up = [40 + 80 * i / (n - 1) for i in range(n)]
    stock = pd.DataFrame({"open": up, "high": [c + 0.5 for c in up],
                          "low": [c - 0.5 for c in up], "close": up,
                          "volume": [1_000_000] * n})
    down = [200 - 150 * i / (n - 1) for i in range(n)]  # falling index (below its 200SMA)
    index = pd.DataFrame({"open": down, "high": [c + 0.5 for c in down],
                          "low": [c - 0.5 for c in down], "close": down,
                          "volume": [1] * n})
    with_regime = btcache.find_signals(stock, index, apply_regime=True)
    without = btcache.find_signals(stock, index, apply_regime=False)
    assert len(with_regime) == 0        # all entries sat out in a down-regime
    assert len(without) >= len(with_regime)
