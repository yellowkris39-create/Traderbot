"""Bars + signal cache for fast backtest iteration.

Two bottlenecks, two caches:
  * network — `build_cache` fetches ~850 symbols' bars ONCE to pickle.
  * CPU — entry-signal detection (evaluate_symbol on every bar) is the real cost
    and is INDEPENDENT of the exit knobs. `build_signal_cache` computes signals
    ONCE per symbol; then every exit-parameter variant is a cheap second pass
    (simulate only). This makes a full-universe sweep run in seconds, not hours.

    # server (network), once:
    python -m brokebyte.screener.btcache build
    # CPU, once (after build):
    python -m brokebyte.screener.btcache signals
    # fast thereafter (seconds):
    python -m brokebyte.screener.btcache sweep
"""

from __future__ import annotations

import pickle
from pathlib import Path

import pandas as pd

from brokebyte.screener import backtest as bt
from brokebyte.screener import screen
from brokebyte.screener.data import Fundamentals
from brokebyte.screener.screen import evaluate_symbol, index_symbol_for

DEFAULT_DIR = Path(__file__).with_name("bars_cache")
_DUMMY_FUNDS = Fundamentals(market_cap=None, beta=None, next_earnings=None, currency="USD")


def _safe(symbol: str) -> str:
    return "".join(c if (c.isalnum() or c in ".-") else "_" for c in symbol)


def cache_path(cache_dir: Path, symbol: str) -> Path:
    return Path(cache_dir) / (_safe(symbol) + ".pkl")


def signals_path(cache_dir: Path, symbol: str) -> Path:
    return Path(cache_dir) / (_safe(symbol) + ".signals.pkl")


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
    """Fetch each symbol + its index and pickle to cache_dir (server, network)."""
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


# --- Signal detection (the expensive, exit-independent part) ---------------

def find_signals(bars, index_bars) -> list:
    """Every qualifying entry as (entry_idx, entry_price, stop_price), WITHOUT
    overlap-skipping. Entry = next bar open; stop = swing-low plan stop. These
    depend only on the rules, not on the exit knobs — so they're computed once
    and reused across all exit variants."""
    out = []
    n = len(bars)
    for t in range(screen.MIN_BARS, n - 1):
        res = evaluate_symbol("?", bars.iloc[: t + 1], index_bars.iloc[: t + 1], _DUMMY_FUNDS, skip_universe=True)
        if not res.passed or res.plan is None:
            continue
        entry_price = float(bars["open"].iloc[t + 1])
        stop_price = res.plan.stop_price
        if stop_price >= entry_price:
            continue
        out.append((t + 1, entry_price, stop_price))
    return out


def simulate_from_signals(bars, signals, *, symbol="?", target_rr=2.0, max_holding_days=10, breakeven_at_r=1.0, trail_at_r=1.5, trail_pct=0.02):
    """Apply exit knobs to precomputed signals, honouring no-overlap (next entry
    must be after the previous trade's exit). Reproduces backtest_symbol exactly
    for the same params, but with no per-bar signal recomputation."""
    trades = []
    next_allowed = 0
    for entry_idx, entry_price, stop_price in signals:
        if entry_idx < next_allowed:
            continue
        risk = entry_price - stop_price
        target_price = entry_price + target_rr * risk
        trade = bt.simulate_trade(bars, entry_idx, entry_price, stop_price, target_price, symbol=symbol, max_holding_days=max_holding_days, breakeven_at_r=breakeven_at_r, trail_at_r=trail_at_r, trail_pct=trail_pct)
        trades.append(trade)
        next_allowed = trade.exit_idx + 1
    return trades


def build_signal_cache(symbols, cache_dir: Path = DEFAULT_DIR) -> int:
    """Compute + pickle signals for each symbol from cached bars (CPU, once)."""
    index_cache = {}
    n = 0
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
        sigs = find_signals(bars, index_bars)
        with signals_path(cache_dir, sym).open("wb") as fh:
            pickle.dump(sigs, fh)
        n += 1
    print("build_signal_cache: wrote signals for {} symbols".format(n))
    return n


def load_signals(cache_dir: Path, symbol: str):
    p = signals_path(cache_dir, symbol)
    if not p.exists():
        return None
    try:
        with p.open("rb") as fh:
            return pickle.load(fh)
    except Exception:
        return None


def backtest_from_signals(symbols, cache_dir: Path = DEFAULT_DIR, *, train_frac=0.65, **params):
    """Fast backtest from cached bars + cached signals (no signal recompute)."""
    all_t, is_t, oos_t = [], [], []
    for sym in symbols:
        bars = load_cached_bars(cache_dir, sym)
        sigs = load_signals(cache_dir, sym)
        if bars is None or sigs is None:
            continue
        trades = simulate_from_signals(bars, sigs, symbol=sym, **params)
        split_idx = int(len(bars) * train_frac)
        is_s, oos_s = bt.partition_trades(trades, split_idx)
        all_t.extend(trades)
        is_t.extend(is_s)
        oos_t.extend(oos_s)
    return all_t, is_t, oos_t


DEFAULT_GRID = [
    {"label": "baseline"},
    {"label": "no-ladder (winners run)", "breakeven_at_r": 99.0, "trail_at_r": 99.0},
    {"label": "hold-20d", "max_holding_days": 20},
    {"label": "target-3R", "target_rr": 3.0},
    {"label": "looser-trail-3pct", "trail_pct": 0.03},
]


def sweep(symbols, cache_dir: Path = DEFAULT_DIR, grid=None, *, train_frac=0.65) -> str:
    """Run each variant against the SIGNAL cache (fast). Decide on OOS exp."""
    grid = grid or DEFAULT_GRID
    lines = ["variant                     |  OOS n  OOS win  OOS exp  | OVR exp  OVR total"]
    for variant in grid:
        params = {k: v for k, v in variant.items() if k != "label"}
        all_t, is_t, oos_t = backtest_from_signals(symbols, cache_dir, train_frac=train_frac, **params)
        o = bt.compute_metrics(oos_t)
        a = bt.compute_metrics(all_t)
        lines.append("{:27} | {:5d}  {:6.1%}  {:+6.3f}R | {:+6.3f}R  {:+6.1f}R".format(variant.get("label", "?"), o.trades, o.win_rate, o.expectancy_r, a.expectancy_r, a.total_r))
    return "\n".join(lines)


def report(symbols, cache_dir: Path = DEFAULT_DIR, *, train_frac=0.65, **params) -> str:
    all_t, is_t, oos_t = backtest_from_signals(symbols, cache_dir, train_frac=train_frac, **params)
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


if __name__ == "__main__":
    import sys
    from brokebyte.screener.universe import load_universe
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sweep"
    syms = load_universe()
    if cmd == "build":
        build_cache(syms)
    elif cmd == "signals":
        build_signal_cache(syms)
    elif cmd == "sweep":
        print(sweep(syms))
    else:
        print(report(syms))
