"""Bars cache for fast backtest iteration.

The full backtest re-fetches ~850 symbols from yfinance (~90 min). For tuning we
want to fetch ONCE then run many parameter variants in seconds. build_cache()
saves each symbol's (and the needed index's) daily bars to a pickle; the cached
backtest + sweep read only from disk (no network), so they unit-test offline.

    # server (network): fetch once
    python -m brokebyte.screener.btcache build
    # then iterate fast (seconds), e.g. disable the exit ladder:
    python -m brokebyte.screener.btcache sweep
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from brokebyte.screener import backtest as bt
from brokebyte.screener.screen import index_symbol_for

DEFAULT_DIR = Path(__file__).with_name("bars_cache")


def _safe(symbol: str) -> str:
    return "".join(c if (c.isalnum() or c in ".-") else "_" for c in symbol)


def cache_path(cache_dir: Path, symbol: str) -> Path:
    return Path(cache_dir) / (_safe(symbol) + ".pkl")


def save_bars(cache_dir: Path, symbol: str, bars: pd.DataFrame) -> None:
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    bars.to_pickle(cache_path(cache_dir, symbol))


def load_cached_bars(cache_dir: Path, symbol: str):
    p = cache_path(cache_dir, symbol)
    if not p.exists():
        return None
    try:
        return pd.read_pickle(p)
    except Exception:
        return None


def build_cache(symbols, cache_dir: Path = DEFAULT_DIR, provider=None, lookback_days: int = 2000) -> int:
    """Fetch each symbol + its index and pickle to cache_dir. Server-side
    (needs network). Returns the number of symbols cached."""
    if provider is None:
        from brokebyte.screener.yfinance_provider import YFinanceProvider
        provider = YFinanceProvider()
    wanted = set(symbols) | {index_symbol_for(s) for s in symbols}
    n = 0
    for sym in sorted(wanted):
        try:
            bars = provider.daily_bars(sym, lookback_days=lookback_days)
            if bars is not None and len(bars):
                save_bars(cache_dir, sym, bars)
                n += 1
        except Exception as exc:  # noqa: BLE001
            print("build_cache {}: error {}".format(sym, exc))
    print("build_cache: cached {} of {} symbols to {}".format(n, len(wanted), cache_dir))
    return n


def backtest_from_cache(symbols, cache_dir: Path = DEFAULT_DIR, *, train_frac=0.65, **params):
    """Run the backtest from cached bars (no network). Returns
    (all_trades, in_sample, out_of_sample). `params` are the exit knobs
    (target_rr, max_holding_days, breakeven_at_r, trail_at_r, trail_pct)."""
    all_t, is_t, oos_t = [], [], []
    index_cache = {}
    for sym in symbols:
        bars = load_cached_bars(cache_dir, sym)
        if bars is None or len(bars) == 0:
            continue
        idx_sym = index_symbol_for(sym)
        if idx_sym not in index_cache:
            index_cache[idx_sym] = load_cached_bars(cache_dir, idx_sym)
        index_bars = index_cache[idx_sym]
        if index_bars is None or len(index_bars) == 0:
            continue
        trades = bt.backtest_symbol(bars, index_bars, symbol=sym, **params)
        split_idx = int(len(bars) * train_frac)
        is_s, oos_s = bt.partition_trades(trades, split_idx)
        all_t.extend(trades)
        is_t.extend(is_s)
        oos_t.extend(oos_s)
    return all_t, is_t, oos_t


def report_from_cache(symbols, cache_dir: Path = DEFAULT_DIR, *, train_frac=0.65, **params) -> str:
    all_t, is_t, oos_t = backtest_from_cache(symbols, cache_dir, train_frac=train_frac, **params)
    lines = [
        "params: " + str(params),
        bt._fmt("OVERALL", bt.compute_metrics(all_t)),
        bt._fmt("IN-SAMPLE", bt.compute_metrics(is_t)),
        bt._fmt("OUT-SAMPLE", bt.compute_metrics(oos_t)),
        "exits by reason (count, avgR, totalR):",
    ]
    for reason, (cnt, avg_r, tot_r) in bt.breakdown_by_reason(all_t).items():
        lines.append("    {:18} n={:4d}  avg {:+.2f}R  total {:+.1f}R".format(reason, cnt, avg_r, tot_r))
    return "\n".join(lines)


# One-lever variants to try (each changes ONE thing vs the baseline).
DEFAULT_GRID = [
    {"label": "baseline"},
    {"label": "no-ladder (winners run)", "breakeven_at_r": 99.0, "trail_at_r": 99.0},
    {"label": "hold-20d", "max_holding_days": 20},
    {"label": "target-3R", "target_rr": 3.0},
    {"label": "looser-trail-3pct", "trail_pct": 0.03},
]


def sweep(symbols, cache_dir: Path = DEFAULT_DIR, grid=None, *, train_frac=0.65) -> str:
    """Run each variant against the cache; one compact line each. Decide on the
    OUT-OF-SAMPLE expectancy column, not OVERALL."""
    grid = grid or DEFAULT_GRID
    lines = ["variant                     |  OOS n  OOS win  OOS exp  | OVR exp  OVR total"]
    for variant in grid:
        params = {k: v for k, v in variant.items() if k != "label"}
        all_t, is_t, oos_t = backtest_from_cache(symbols, cache_dir, train_frac=train_frac, **params)
        o = bt.compute_metrics(oos_t)
        a = bt.compute_metrics(all_t)
        lines.append("{:27} | {:5d}  {:6.1%}  {:+6.3f}R | {:+6.3f}R  {:+6.1f}R".format(
            variant.get("label", "?"), o.trades, o.win_rate, o.expectancy_r, a.expectancy_r, a.total_r))
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    from brokebyte.screener.universe import load_universe
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sweep"
    syms = load_universe()
    if cmd == "build":
        build_cache(syms)
    elif cmd == "sweep":
        print(sweep(syms))
    else:
        print(report_from_cache(syms))
