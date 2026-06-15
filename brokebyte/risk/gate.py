"""Risk gate — Milestone 1 stub.

Intentionally minimal: fixed share size, simple HOLD-by-default logic.
The real risk module (volatility-based sizing, stop-loss/take-profit,
exposure limits, circuit breakers) is built in Phase 2 with unit tests
written first, before anything else is wired to it.
"""

from __future__ import annotations

from dataclasses import dataclass

from brokebyte.llm.provider import Direction, LLMVerdict

# Milestone 1 only: fixed, tiny size. Replaced by volatility-based sizing in Phase 2.
FIXED_QTY = 1


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str  # "buy" | "sell"
    qty: float


def evaluate(verdict: LLMVerdict) -> OrderIntent | None:
    """Return an OrderIntent, or None to HOLD.

    Default is HOLD: proceeds only if the verdict is material, names a
    symbol, has a non-neutral direction, and isn't already priced in.
    """
    if not verdict.material:
        return None
    if verdict.symbol is None:
        return None
    if verdict.direction == Direction.NONE:
        return None
    if verdict.is_already_priced_in:
        return None

    side = "buy" if verdict.direction == Direction.LONG else "sell"
    return OrderIntent(symbol=verdict.symbol, side=side, qty=FIXED_QTY)
