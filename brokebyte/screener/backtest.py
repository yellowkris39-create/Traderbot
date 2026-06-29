"""Walk-forward backtest for the screener ruleset.

Replays daily bars one day at a time, fires the SAME rules the live screener
uses (via evaluate_symbol(..., skip_universe=True) — point-in-time fundamentals
can't be reconstructed, so universe filters are dropped and only the
bar-derived trend/setup/trigger are tested), then simulates the exit ladder
(stop / target / break-even / trailing / time-stop) and reports R-multiples.

Everything is pure (operates on DataFrames) so it unit-tests without network.

ASSUMPTIONS / LIMITATIONS (state them honestly — this is not a perfect sim):
  * Entry is the NEXT bar's open after a signal (no look-ahead).
  * If a bar's range touches BOTH stop and target, the STOP is assumed hit
    first (conservative).
  * Break-even / trailing stops are evaluated on each bar's CLOSE, then applied
    to the following bar — they cannot use intrabar highs.
  * No commission, no slippage, no spread. R-multiple = (exit-entry)/(entry-stop).
  * Only one position per symbol at a time (no pyramiding).
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


def simulate_trade(
    bars: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    stop_price: float,
    target_price: float,
    *,
    symbol: str = "?",
    max_holding_days: int = 10,
    breakeven_at_r: float = 1.0,
    trail_at_r: float = 1.5,
    trail_pct: float = 0.02,
) -> BacktestTrade:
    """Simulate one long trade from entry_idx forward. Returns the resulting
    BacktestTrade (exit reason: stop | target | time_stop | end_of_data)."""
    risk = entry_price - stop_price
    current_stop = stop_price
    opened = datetime(2020, 1, 1, tzinfo=timezone.utc)  # only the day-COUNT matters

    last = len(bars) - 1
    for i in range(entry_idx + 1, len(bars)):
        high = float(bars["high"].iloc[i])
        low = float(bars["low"].iloc[i])
        close = float(bars["close"].iloc[i])
        held = i - entry_idx

        # 1) stop first (conservative when both touched), then target
        if low <= current_stop:
            exit_price = current_stop
            return BacktestTrade(symbol, entry_idx, entry_price, stop_price,
                                 target_price, i, exit_price,
                                 "stop" if current_stop == stop_price else "trail_or_be_stop",
                                 (exit_price - entry_price) / risk, held)
        if high >= target_price:
            return BacktestTrade(symbol, entry_idx, entry_price, stop_price,
                                 target_price, i, target_price, "target",
                                 (target_price - entry_price) / risk, held)

        # 2) time-stop at the close of day `max_holding_days`
        if held >= max_holding_days:
            return BacktestTrade(symbol, entry_idx, entry_price, stop_price,
                                 target_price, i, close, "time_stop",
                                 (close - entry_price) / risk, held)

        # 3) ratchet the stop using this close, applied to the next bar
        action = exits.decide_exit(
            side="buy", entry_price=entry_price, stop_price=stop_price,
            current_stop_price=current_stop, current_price=close,
            opened_at=opened, now=opened,  # disable time-stop here; handled above
            max_holding_days=10**6,
            breakeven_at_r=breakeven_at_r, trail_at_r=trail_at_r, trail_pct=trail_pct,
        )
        if action.kind == exits.MOVE_BREAKEVEN and action.new_stop_price is not None:
            current_stop = action.new_stop_price

    # ran out of data
    close = float(bars["close"].iloc[last])
    return BacktestTrade(symbol, entry_idx, entry_price, stop_price, target_price,
                         last, close, "end_of_data", (close - entry_price) / risk,
                         last - entry_idx)


def backtest_symbol(
    bars: pd.DataFrame,
    index_bars: pd.DataFrame,
    *,
    symbol: str = "?",
    max_holding_days: int = 10,
) -> list[BacktestTrade]:
    """Walk forward; on each qualifying signal, enter next-open and simulate to
    exit. Skips ahead past each trade so positions don't overlap."""
    trades: list[BacktestTrade] = []
    t = screen.MIN_BARS
    n = len(bars)
    while t < n - 1:
        window = bars.iloc[: t + 1]
        idx_window = index_bars.iloc[: t + 1]
        res = evaluate_symbol(symbol, window, idx_window, _DUMMY_FUNDS, skip_universe=True)
        if not res.passed or res.plan is None:
            t += 1
            continue
        entry_price = float(bars["open"].iloc[t + 1])
        stop_price = res.plan.stop_price
        if stop_price >= entry_price:  # gap below stop at open -> skip
            t += 1
            continue
        risk = entry_price - stop_price
        target_price = entry_price + 2 * risk
        trade = simulate_trade(bars, t + 1, entry_price, stop_price, target_price,
                               symbol=symbol, max_holding_days=max_holding_days)
        trades.append(trade)
        t = trade.exit_idx + 1  # no overlapping positions
    return trades


def compute_metrics(trades: list[BacktestTrade]) -> BacktestMetrics:
    if not trades:
        return BacktestMetrics(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    rs = [t.r_multiple for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    total = sum(rs)
    return BacktestMetrics(
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / len(rs),
        avg_r=total / len(rs),
        total_r=total,
        expectancy_r=total / len(rs),
        best_r=max(rs),
        worst_r=min(rs),
    )


def run_cli(symbols: list[str], *, max_holding_days: int = 10) -> str:
    """Fetch history via yfinance and backtest each symbol; return a report.
    Runs on the server (yfinance needs network). Used by `python -m
    brokebyte.screener.backtest SYM1 SYM2 ...`."""
    from brokebyte.screener.screen import index_symbol_for
    from brokebyte.screener.yfinance_provider import YFinanceProvider

    provider = YFinanceProvider()
    index_cache: dict[str, pd.DataFrame] = {}
    all_trades: list[BacktestTrade] = []
    lines: list[str] = []
    for sym in symbols:
        try:
            bars = provider.daily_bars(sym, lookback_days=2000)
            idx_sym = index_symbol_for(sym)
            if idx_sym not in index_cache:
                index_cache[idx_sym] = provider.daily_bars(idx_sym, lookback_days=2000)
            trades = backtest_symbol(bars, index_cache[idx_sym], symbol=sym,
                                     max_holding_days=max_holding_days)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{sym}: error {exc}")
            continue
        all_trades.extend(trades)
        m = compute_metrics(trades)
        lines.append(f"{sym}: {m.trades} trades, win {m.win_rate:.0%}, avg {m.avg_r:+.2f}R, total {m.total_r:+.1f}R")

    overall = compute_metrics(all_trades)
    lines.append("")
    lines.append(f"OVERALL: {overall.trades} trades, win rate {overall.win_rate:.1%}, "
                 f"avg {overall.avg_r:+.2f}R, expectancy {overall.expectancy_r:+.2f}R, "
                 f"total {overall.total_r:+.1f}R (best {overall.best_r:+.1f}, worst {overall.worst_r:+.1f})")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    from brokebyte.screener.universe import load_universe
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    syms = args or load_universe()
    print(run_cli(syms))
