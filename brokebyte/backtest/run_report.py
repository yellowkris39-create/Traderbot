"""End-to-end Track A report: fetch historical bars and run the walk-forward
mechanical backtest (SPEC.md Sec 5 / Sec 6 build order step 5).

Run with:
    venv\\Scripts\\python.exe -m brokebyte.backtest.run_report [SYMBOL] [N_WINDOWS]

Defaults to AAPL over the last 3 years, split into 4 walk-forward windows.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from brokebyte.backtest.costs import CostModel
from brokebyte.backtest.metrics import compute_metrics
from brokebyte.backtest.walkforward import run_walkforward
from brokebyte.config import load_config
from brokebyte.execution.market_data import MarketData
from brokebyte.risk.limits import load_risk_limits


def main(symbol: str = "AAPL", n_windows: int = 4, lookback_days: int = 3 * 365) -> None:
    config = load_config()
    limits = load_risk_limits()
    cost_model = CostModel()
    market_data = MarketData(config)

    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=lookback_days)
    bars = market_data.get_historical_bars(symbol, start, end)

    print(f"{symbol}: {len(bars)} daily bars from {start.date()} to {end.date()}")
    if bars.empty:
        print("No bars returned; nothing to backtest.")
        return

    windows = run_walkforward(bars, symbol, limits, cost_model, n_windows=n_windows)

    all_trades = []
    for idx, window in enumerate(windows):
        m = window.metrics
        print(
            f"\nWindow {idx + 1}/{n_windows}: bars {window.start_index}-{window.end_index - 1} "
            f"({window.start_label} -> {window.end_label})"
        )
        print(f"  regime coverage: {dict(window.regime_counts)}")
        print(f"  trades: {m.trade_count}, win_rate: {m.win_rate:.2%}")
        print(f"  profit_factor: {m.profit_factor}, expectancy: ${m.expectancy:.2f}")
        print(f"  sharpe: {m.sharpe_ratio}, sortino: {m.sortino_ratio}")
        print(f"  max_drawdown: {m.max_drawdown_pct:.2%}, recovery_trades: {m.max_drawdown_recovery_trades}")
        print(f"  total_return: {m.total_return_pct:.2%}")
        all_trades.extend(window.result.trades)

    combined = compute_metrics(all_trades, initial_equity=100_000.0)
    print(
        f"\nAll windows combined ({combined.trade_count} trades total; each window's "
        "equity curve restarts at $100,000, so this is a per-trade rollup, not a "
        "single continuous equity curve):"
    )
    print(f"  win_rate: {combined.win_rate:.2%}, profit_factor: {combined.profit_factor}")
    print(f"  expectancy: ${combined.expectancy:.2f}, sortino: {combined.sortino_ratio}")


if __name__ == "__main__":
    cli_symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    cli_n_windows = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    main(cli_symbol, cli_n_windows)
