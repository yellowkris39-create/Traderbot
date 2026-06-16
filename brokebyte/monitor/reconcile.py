"""Position-monitoring reconciliation loop (Module 7 / Phase 5e).

Polls Alpaca for open positions and reconciles them against ENTER decisions
in DecisionStore that have no recorded outcome yet.  When a position has
closed (no longer appears in Alpaca positions), the exit fill is fetched,
the exit reason is inferred, P&L is computed, and
DecisionStore.record_outcome() is called — activating Track B's forward-paper
soak metrics, Module 7 retrieval, and the calibration layer.

Run with:
    venv\\Scripts\\python.exe -m brokebyte.monitor

Design notes:
- Reconciliation is symbol-based: an open ENTER decision whose verdict_symbol
  is not in the current Alpaca positions is considered closed.
- broker_order_id is stored in DecisionStore (populated by main.py after order
  submission) for future exact-order lookup enhancement; the current reconciler
  uses the simpler symbol + recorded_at window approach.
- PositionBrokerLike is a Protocol so the reconciler can be unit-tested without
  a real Alpaca connection (pass a FakeBroker).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from brokebyte.common import FilledOrder
from brokebyte.logging_setup import get_logger
from brokebyte.memory.store import DecisionStore


class PositionBrokerLike(Protocol):
    """Minimal broker interface needed for position reconciliation."""

    def get_position_symbols(self) -> set[str]: ...

    def get_filled_exit_order(
        self, symbol: str, after: datetime, plan_side: str
    ) -> FilledOrder | None: ...


@dataclass(frozen=True)
class OutcomeRecord:
    decision_id: int
    symbol: str
    exit_price: float
    exit_reason: str
    pnl: float


def _infer_exit_reason(
    fill_price: float,
    stop_price: float,
    take_profit_price: float,
) -> str:
    """Return 'take_profit' or 'stop' by choosing whichever plan price is closer
    to the actual fill.  Works for both long and short positions."""
    dist_stop = abs(fill_price - stop_price)
    dist_tp = abs(fill_price - take_profit_price)
    return "take_profit" if dist_tp <= dist_stop else "stop"


def _compute_pnl(
    entry_price: float,
    exit_price: float,
    qty: int,
    plan_side: str,
) -> float:
    if plan_side == "buy":
        return (exit_price - entry_price) * qty
    return (entry_price - exit_price) * qty  # short


def reconcile_open_positions(
    broker: PositionBrokerLike,
    store: DecisionStore,
    log=None,
) -> list[OutcomeRecord]:
    """Reconcile open ENTER decisions against the current Alpaca positions.

    For each ENTER decision with no recorded outcome:
    - If the symbol still has an open position → skip (still live).
    - If the symbol has no open position → closed; find the exit fill,
      record the outcome, and return an OutcomeRecord.

    Returns a list of outcomes recorded in this call (empty if no positions
    closed since the last run).
    """
    if log is None:
        log = get_logger("brokebyte.monitor")

    open_decisions = store.open_enter_decisions()
    if not open_decisions:
        log.info("monitor_reconcile", open_decisions=0, outcomes_recorded=0)
        return []

    current_symbols = broker.get_position_symbols()
    outcomes: list[OutcomeRecord] = []

    for row in open_decisions:
        symbol = row["verdict_symbol"]
        if not symbol:
            log.warning("monitor_skip_no_symbol", decision_id=row["id"])
            continue

        if symbol in current_symbols:
            continue  # position still open

        # Position has closed — find the fill details.
        recorded_at = datetime.fromisoformat(row["recorded_at"])
        plan_side = row["plan_side"] or "buy"

        exit_order = broker.get_filled_exit_order(symbol, recorded_at, plan_side)
        if exit_order is None:
            log.warning(
                "monitor_no_exit_order",
                decision_id=row["id"],
                symbol=symbol,
            )
            continue

        exit_reason = _infer_exit_reason(
            exit_order.fill_price,
            float(row["plan_stop_price"]),
            float(row["plan_take_profit_price"]),
        )
        pnl = _compute_pnl(
            float(row["plan_entry_price"]),
            exit_order.fill_price,
            int(row["plan_qty"]),
            plan_side,
        )

        store.record_outcome(
            row["id"],
            exit_price=exit_order.fill_price,
            exit_reason=exit_reason,
            pnl=pnl,
            closed_at=exit_order.filled_at,
        )

        log.info(
            "position_outcome_recorded",
            decision_id=row["id"],
            symbol=symbol,
            exit_reason=exit_reason,
            exit_price=exit_order.fill_price,
            pnl=pnl,
        )
        outcomes.append(
            OutcomeRecord(
                decision_id=row["id"],
                symbol=symbol,
                exit_price=exit_order.fill_price,
                exit_reason=exit_reason,
                pnl=pnl,
            )
        )

    log.info(
        "monitor_reconcile",
        open_decisions=len(open_decisions),
        outcomes_recorded=len(outcomes),
    )
    return outcomes
