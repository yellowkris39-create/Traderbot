"""Track B §5a report: success metrics + promotion check over the
DecisionStore (SPEC.md Sec 5a / Sec 6 build order step 5).

Run with:
    venv\\Scripts\\python.exe -m brokebyte.memory.run_report [DB_PATH]

Defaults to logs/decisions.db. Until a future position-monitoring phase
starts calling DecisionStore.record_outcome for closed trades, expect
"0 with closed-trade outcomes" and an INSUFFICIENT DATA promotion status --
that is the correct, honest answer at this point in the soak, not an error.
"""

from __future__ import annotations

import sys

from brokebyte.backtest.metrics import DEFAULT_THRESHOLDS, evaluate_promotion
from brokebyte.memory.calibration import MIN_CALIBRATION_SAMPLE, compute_calibration
from brokebyte.memory.metrics import compute_decision_store_metrics
from brokebyte.memory.store import DecisionStore


def main(db_path: str = "logs/decisions.db") -> None:
    store = DecisionStore(db_path)

    total = store.count()
    metrics = compute_decision_store_metrics(store)
    coverage = store.regime_coverage()

    print(f"{db_path}: {total} decisions recorded, {metrics.trade_count} with closed-trade outcomes")
    print(f"regime coverage (all decisions reaching Module 3): {dict(coverage)}")
    print(f"win_rate: {metrics.win_rate:.2%}, profit_factor: {metrics.profit_factor}")
    print(f"expectancy: ${metrics.expectancy:.2f}, sortino: {metrics.sortino_ratio}, sharpe: {metrics.sharpe_ratio}")
    print(f"max_drawdown: {metrics.max_drawdown_pct:.2%}, recovery_trades: {metrics.max_drawdown_recovery_trades}")
    print(f"total_return: {metrics.total_return_pct:.2%}")

    check = evaluate_promotion(metrics, coverage, DEFAULT_THRESHOLDS)
    print(f"\nSec 5a promotion check (thresholds: {DEFAULT_THRESHOLDS}):")
    if not check.sufficient_data:
        print("  status: INSUFFICIENT DATA")
    elif check.passed:
        print("  status: PASS")
    else:
        print("  status: FAIL")
    for failure in check.failures:
        print(f"  - {failure}")

    cal = compute_calibration(store)
    print(f"\nModule 7 calibration (advisory, min sample={MIN_CALIBRATION_SAMPLE} per bucket):")
    if not cal.sufficient_data:
        print("  status: INSUFFICIENT DATA (no bucket has reached min sample)")
    else:
        print("  status: data available")
    if cal.by_regime:
        print("  by regime:")
        for regime, stats in cal.by_regime.items():
            flag = "" if stats.count >= MIN_CALIBRATION_SAMPLE else " [low data]"
            print(f"    {regime}: n={stats.count} win_rate={stats.win_rate:.2%} mean_pnl=${stats.mean_pnl:.2f}{flag}")
    else:
        print("  by regime: (no closed ENTER decisions yet)")
    print("  by confidence bucket:")
    for bucket, stats in cal.by_confidence.items():
        flag = "" if stats.count >= MIN_CALIBRATION_SAMPLE else " [low data]"
        print(f"    {bucket}: n={stats.count} win_rate={stats.win_rate:.2%} mean_pnl=${stats.mean_pnl:.2f}{flag}")


if __name__ == "__main__":
    cli_db_path = sys.argv[1] if len(sys.argv) > 1 else "logs/decisions.db"
    main(cli_db_path)
