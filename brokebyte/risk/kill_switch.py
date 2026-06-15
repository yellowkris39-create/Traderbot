"""Kill switch (Module 4): flattens all positions and cancels all open
orders. Triggered by a circuit breaker (Module 11) or the daily-loss halt
(risk/portfolio.py) — never invoked for routine HOLD decisions.

Operates on anything exposing Alpaca's TradingClient
close_all_positions/cancel_orders signatures, so it's testable with a fake
client and has no live-broker dependency of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TradingClientLike(Protocol):
    def close_all_positions(self, cancel_orders: bool | None = None): ...

    def cancel_orders(self): ...


@dataclass(frozen=True)
class KillSwitchResult:
    reason: str
    positions_closed: int
    orders_cancelled: int


def execute_kill_switch(client: TradingClientLike, reason: str) -> KillSwitchResult:
    """Flatten every position (cancelling bracket legs) and cancel any
    remaining open orders. Returns a summary for logging/alerting."""
    closed = client.close_all_positions(cancel_orders=True)
    cancelled = client.cancel_orders()
    return KillSwitchResult(
        reason=reason,
        positions_closed=len(closed),
        orders_cancelled=len(cancelled),
    )
