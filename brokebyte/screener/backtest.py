"""Walk-forward backtest + iteration tooling for the screener ruleset.

Replays daily bars one day at a time, fires the SAME rules the live screener
uses (evaluate_symbol(skip_universe=True) — point-in-time fundamentals can't be
reconstructed), simulates the exit ladder, and reports R-multiples.

Iteration discipline built in:
  * exit knobs (target_rr, breakeven_at_r, trail_at_r, trail_pct,
    max_holding_days) are parameters, so you change ONE lever at a time;
  * trades are partitioned IN-SAMPLE vs OUT-OF-SAMPLE by entry date, so a
    change that only helps in-sample (overfit) is visible immediately.

Pure (operates on DataFrames) so it unit-tests without network.

LIMITATIONS: entry = next bar open; stop assumed first when a bar straddles
stop & target; break-even/trailing on closes; NO commission/slippage; universe
filters skipped -> live is stricter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from brokebyte.monitor import exits
from brokebyte.screener import screen
from brokebyte.screener.data import Fundamentals
from brokebyte.screener.screen import evaluate_symbol

_DUMMY_FUNDS = Fundamentals(market_cap=None, beta=None, next_earnings=None, currency="USD")


@dataclass(frozen=True)
class BacktestTrade:
    symbol: str
    entry_idx: int
    entry_price: float
    stop_price: float
    target_price: float
    exit_idx: int
    exit_price: float
    exit_reason: str
    r_multiple: float
    bars_held: int


@dataclass(frozen=True)
class BacktestMetrics:
    trades: int
    wins: int
    losses: int
    win_rate: float
    avg_r: float
    total_r: float
    expectancy_r: float
    best_r: float
    worst_r: float
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0


def simulate_trade(bars, entry_idx, entry_price, stop_price, target_price, *, symbol="?", max_holding_days=10, breakeven_at_r=1.0, trail_at_r=1.5, trail_pct=0.02):
    """Simulate one long trade from entry_idx forward."""
    risk = entry_price - stop_price
    current_stop = stop_price
    opened = datetime(2020, 1, 1, tzinfo=timezone.utc)

    last = len(bars) - 1
    for i in range(entry_idx + 1, len(bars)):
        high = float(bars["high"].iloc[i])
        low = float(bars["low"].iloc[i])
        close = float(bars["close"].iloc[i])
        held = i - entry_idx

        if low <= current_stop:
            reason = "stop" if current_stop == stop_price else "trail_or_be_stop"
            return BacktestTrade(symbol, entry_idx, entry_price, stop_price, target_price, i, current_stop, reason, (current_stop - entry_price) / risk, held)
        if high >= target_price:
            return BacktestTrade(symbol, entry_idx, entry_price, stop_price, target_price, i, target_price, "target", (target_price - entry_price) / risk, held)
        if held >= max_holding_days:
            return BacktestTrade(symbol, entry_idx, entry_price, stop_price, target_price, i, close, "time_stop", (close - entry_price) / risk, held)

        action = exits.decide_exit(side="buy", entry_price=entry_price, stop_price=stop_price, current_stop_price=current_stop, current_price=close, opened_at=opened, now=opened, max_holding_days=10**6, breakeven_at_r=breakeven_at_r, trail_at_r=trail_at_r, trail_pct=trail_pct)
        if action.kind == exits.MOVE_BREAKEVEN and action.new_stop_price is not None:
            current_stop = action.new_stop_price

    close = float(bars["close"].iloc[last])
    return BacktestTrade(symbol, entry_idx, entry_price, stop_price, target_price, last, close, "end_of_data", (close - entry_price) / risk, last - entry_idx)


def backtest_symbol(bars, index_bars, *, symbol="?", target_rr=2.0, max_holding_days=10, breakeven_at_r=1.0, trail_at_r=1.5, trail_pct=0.02):
    """Walk forward; enter next-open on each qualifying signal, simulate to exit,
    skip past it (no overlap). `target_rr` sets the take-profit reward:risk."""
    trades = []
    t = screen.MIN_BARS
    n = len(bars)
    while t < n - 1:
        res = evaluate_symbol(symbol, bars.iloc[: t + 1], index_bars.iloc[: t + 1], _DUMMY_FUNDS, skip_universe=True)
        if not res.passed or res.plan is None:
            t += 1
            continue
        entry_price = float(bars["open"].iloc[t + 1])
        stop_price = res.plan.stop_price
        if stop_price >= entry_price:
            t += 1
            continue
        risk = entry_price - stop_price
        target_price = entry_price + target_rr * risk
        trade = simulate_trade(bars, t + 1, entry_price, stop_price, target_price, symbol=symbol, max_holding_days=max_holding_days, breakeven_at_r=breakeven_at_r, trail_at_r=trail_at_r, trail_pct=trail_pct)
        trades.append(trade)
        t = trade.exit_idx + 1
    return trades


def partition_trades(trades, split_idx):
    """Split trades into (in_sample, out_of_sample) by entry_idx < split_idx."""
    is_ = [t for t in trades if t.entry_idx < split_idx]
    oos = [t for t in trades if t.entry_idx >= split_idx]
    return is_, oos


def compute_metrics(trades):
    if not trades:
        return BacktestMetrics(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    rs = [t.r_multiple for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    total = sum(rs)
    return BacktestMetrics(
        trades=len(trades), wins=len(wins), losses=len(losses),
        win_rate=len(wins) / len(rs), avg_r=total / len(rs), total_r=total,
        expectancy_r=total / len(rs), best_r=max(rs), worst_r=min(rs),
        avg_win_r=(sum(wins) / len(wins)) if wins else 0.0,
        avg_loss_r=(sum(losses) / len(losses)) if losses else 0.0,
    )


def breakdown_by_reason(trades):
    """reason -> (count, avgR, totalR). Shows WHERE expectancy leaks."""
    buckets = {}
    for t in trades:
        buckets.setdefault(t.exit_reason, []).append(t.r_multiple)
    return {k: (len(v), sum(v) / len(v), sum(v)) for k, v in sorted(buckets.items())}


def _fmt(label, m):
    return "{:11} n={:4d}  win {:.1%}  avg {:+.3f}R  exp {:+.3f}R  total {:+.1f}R  (avgW {:+.2f} / avgL {:+.2f})".format(
        label, m.trades, m.win_rate, m.avg_r, m.expectancy_r, m.total_r, m.avg_win_r, m.avg_loss_r)


def run_cli(symbols, *, target_rr=2.0, max_holding_days=10, breakeven_at_r=1.0, trail_at_r=1.5, trail_pct=0.02, train_frac=0.65):
    """Backtest each symbol; report OVERALL, in-sample, out-of-sample, and the
    exit-reason breakdown. Runs on the server (yfinance needs network)."""
    from brokebyte.screener.screen import index_symbol_for
    from brokebyte.screener.yfinance_provider import YFinanceProvider

    provider = YFinanceProvider()
    index_cache = {}
    all_trades, is_trades, oos_trades = [], [], []
    for sym in symbols:
        try:
            bars = provider.daily_bars(sym, lookback_days=2000)
            idx_sym = index_symbol_for(sym)
            if idx_sym not in index_cache:
                index_cache[idx_sym] = provider.daily_bars(idx_sym, lookback_days=2000)
            trades = backtest_symbol(bars, index_cache[idx_sym], symbol=sym, target_rr=target_rr, max_holding_days=max_holding_days, breakeven_at_r=breakeven_at_r, trail_at_r=trail_at_r, trail_pct=trail_pct)
        except Exception as exc:  # noqa: BLE001
            print("{}: error {}".format(sym, exc))
            continue
        split_idx = int(len(bars) * train_frac)
        is_s, oos_s = partition_trades(trades, split_idx)
        all_trades.extend(trades)
        is_trades.extend(is_s)
        oos_trades.extend(oos_s)

    lines = [
        "params: target_rr={} max_hold={} be_at={}R trail_at={}R trail_pct={}".format(target_rr, max_holding_days, breakeven_at_r, trail_at_r, trail_pct),
        _fmt("OVERALL", compute_metrics(all_trades)),
        _fmt("IN-SAMPLE", compute_metrics(is_trades)),
        _fmt("OUT-SAMPLE", compute_metrics(oos_trades)),
        "exits by reason (count, avgR, totalR):",
    ]
    for reason, (cnt, avg_r, tot_r) in breakdown_by_reason(all_trades).items():
        lines.append("    {:18} n={:4d}  avg {:+.2f}R  total {:+.1f}R".format(reason, cnt, avg_r, tot_r))
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    from brokebyte.screener.universe import load_universe
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    syms = args or load_universe()
    print(run_cli(syms))
