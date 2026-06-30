"""Tests for the bars-cache backtest path (offline, synthetic bars)."""

from __future__ import annotations

import pandas as pd

from brokebyte.screener import btcache


def _series(n=260, start=50.0, end=80.0):
    closes = [start + (end - start) * i / (n - 1) for i in range(n)]
    return pd.DataFrame({"open": closes, "high": [c + 0.5 for c in closes],
                         "low": [c - 0.5 for c in closes], "close": closes,
                         "volume": [1_000_000] * n})


def test_safe_filename_for_index_symbol():
    assert btcache._safe("^FTSE") == "_FTSE"
    assert btcache._safe("BT-A.L") == "BT-A.L"


def test_save_load_roundtrip(tmp_path):
    bars = _series()
    btcache.save_bars(tmp_path, "AAPL", bars)
    loaded = btcache.load_cached_bars(tmp_path, "AAPL")
    assert loaded is not None and len(loaded) == len(bars)
    assert btcache.load_cached_bars(tmp_path, "MISSING") is None


def test_backtest_from_cache_runs_offline(tmp_path):
    # cache a stock + its index (SPY for non-.L symbols)
    btcache.save_bars(tmp_path, "AAPL", _series())
    btcache.save_bars(tmp_path, "SPY", _series(start=100, end=100))  # flat index
    all_t, is_t, oos_t = btcache.backtest_from_cache(["AAPL"], tmp_path)
    assert isinstance(all_t, list)
    assert len(all_t) == len(is_t) + len(oos_t)


def test_sweep_outputs_a_line_per_variant(tmp_path):
    btcache.save_bars(tmp_path, "AAPL", _series())
    btcache.save_bars(tmp_path, "SPY", _series(start=100, end=100))
    out = btcache.sweep(["AAPL"], tmp_path)
    # header + one row per default variant
    assert out.count("\n") == len(btcache.DEFAULT_GRID)
    assert "baseline" in out and "no-ladder" in out
