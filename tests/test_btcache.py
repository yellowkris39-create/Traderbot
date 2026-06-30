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
    sigs = btcache.find_signals(bars, idx)
    fast = btcache.simulate_from_signals(bars, sigs, symbol="X", target_rr=2.0)
    assert [t.exit_idx for t in direct] == [t.exit_idx for t in fast]
    assert [round(t.r_multiple, 6) for t in direct] == [round(t.r_multiple, 6) for t in fast]


def test_fast_path_matches_with_different_exit_params():
    bars = _series(n=300, start=40, end=120)
    idx = _series(n=300, start=100, end=100)
    sigs = btcache.find_signals(bars, idx)
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
