"""Bridges Module 7's DecisionStore to the shared §5a metrics
(brokebyte.backtest.metrics), for Track B's forward-paper soak (SPEC.md
Sec 5a / Sec 6 build order step 5).
"""

from __future__ import annotations

from brokebyte.backtest.metrics import PerformanceMetrics, compute_metrics
from brokebyte.memory.store import DecisionStore


def compute_decision_store_metrics(store: DecisionStore, initial_equity: float = 100_000.0) -> PerformanceMetrics:
    """§5a metrics (Sortino, drawdown, profit factor, expectancy, win rate)
    over every decision in `store` with a recorded trade outcome."""
    return compute_metrics(store.closed_trade_pnls(), initial_equity)
