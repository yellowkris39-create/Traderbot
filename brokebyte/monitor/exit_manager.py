"""Active exit management loop (fix for '0 closed trades').

The reconciler only *records* outcomes after a bracket leg fills on its own; it
cannot make a stuck position close. This loop runs each cycle and ACTS: it
raises stops (break-even at +1R, trailing at +1.5R) and force-closes positions
past the 10-trading-day time-stop.

The decision is delegated to the pure `exits.decide_exit`; this module only
talks to the broker (a Protocol, so the orchestration unit-tests with a fake).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from brokebyte.common import FilledOrder
from brokebyte.logging_setup import get_logger
from brokebyte.memory.store import DecisionStore
from brokebyte.monitor import exits


class ExitBrokerLike(Protocol):
    def get_current_price(self, symbol: str) -> float | None: ...
    def get_open_stop(self, order_id: str) -> tuple[str, float] | None: ...
    def replace_stop(self, stop_leg_id: str, new_stop_price: float) -> None: ...
    def flatten(self, symbol: str, order_id: str) -> FilledOrder | None: ...


@dataclass(frozen=True)
class ManageAction:
    decision_id: int
    symbol: str
    kind: str
    detail: str


def _pnl(entry: float, exit_: float, qty: float, side: str) -> float:
    return (exit_ - entry) * qty if side == "buy" else (entry - exit_) * qty


def manage_open_positions(broker: ExitBrokerLike, store: DecisionStore, log=None, *, now: datetime | None = None) -> list[ManageAction]:
    """Apply stop-raises and time-stops to every open ENTER decision that has a
    broker_order_id. Returns the list of actions taken."""
    if log is None:
        log = get_logger("brokebyte.monitor.exit_manager")
    now = now or datetime.now(timezone.utc)

    actions: list[ManageAction] = []
    for row in store.open_enter_decisions():
        order_id = row["broker_order_id"]
        symbol = row["verdict_symbol"]
        if not order_id or not symbol:
            continue

        price = broker.get_current_price(symbol)
        if price is None:
            continue  # position already gone; reconciler books the outcome

        stop = broker.get_open_stop(order_id)
        if stop is None:
            continue  # no live stop leg to reason about
        stop_leg_id, current_stop = stop

        side = row["plan_side"] or "buy"
        action = exits.decide_exit(side=side, entry_price=float(row["plan_entry_price"]), stop_price=float(row["plan_stop_price"]), current_stop_price=float(current_stop), current_price=float(price), opened_at=datetime.fromisoformat(row["recorded_at"]), now=now)

        if action.kind == exits.MOVE_BREAKEVEN and action.new_stop_price is not None:
            broker.replace_stop(stop_leg_id, action.new_stop_price)
            log.info("exit_move_stop", decision_id=row["id"], symbol=symbol, new_stop=action.new_stop_price, reason=action.reason)
            actions.append(ManageAction(row["id"], symbol, action.kind, action.reason))

        elif action.kind == exits.CLOSE_TIME_STOP:
            try:
                fill = broker.flatten(symbol, order_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("exit_time_stop_failed", decision_id=row["id"], symbol=symbol, error=str(exc))
                continue
            if fill is None:
                log.warning("exit_time_stop_no_fill", decision_id=row["id"], symbol=symbol)
                continue
            pnl = _pnl(float(row["plan_entry_price"]), fill.fill_price, float(row["plan_qty"]), side)
            store.record_outcome(row["id"], exit_price=fill.fill_price, exit_reason="time_stop", pnl=pnl, closed_at=fill.filled_at)
            log.info("exit_time_stop_closed", decision_id=row["id"], symbol=symbol, exit_price=fill.fill_price, pnl=pnl)
            actions.append(ManageAction(row["id"], symbol, action.kind, action.reason))

    log.info("exit_manage_cycle", actions=len(actions))
    return actions
