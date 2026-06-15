"""Volatility-based position sizing — Module 4, the core of the risk gate.

Position size is derived from how far away the stop is (ATR-based), not
from "feel" or all-in conviction. Two independent caps apply and the
smaller wins:
  1. Risk-per-trade cap: dollars lost if the stop is hit must stay within
     `max_risk_per_trade_pct` of equity.
  2. Exposure cap: notional of the new position must stay within
     `max_position_pct` of equity.

`size_multiplier` (from the regime guard, Module 9) scales both caps down
in choppy/high-volatility regimes — it can only shrink a position, never
grow it beyond the caps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from brokebyte.risk.limits import RiskLimits


@dataclass(frozen=True)
class PositionPlan:
    symbol: str
    side: str  # "buy" | "sell"
    qty: int
    entry_price: float
    stop_price: float
    take_profit_price: float
    risk_amount: float  # $ lost if stop is hit
    notional: float  # qty * entry_price


def size_position(
    symbol: str,
    side: str,
    entry_price: float,
    atr: float,
    equity: float,
    limits: RiskLimits,
    size_multiplier: float = 1.0,
) -> PositionPlan | None:
    """Return a sized plan with stop/take-profit levels, or None to HOLD
    (no size that respects the risk floors is > 0)."""
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

    if entry_price <= 0 or atr <= 0 or equity <= 0 or size_multiplier <= 0:
        return None

    stop_distance = atr * limits.stop_loss_atr_multiple
    if stop_distance <= 0:
        return None

    risk_dollars = equity * limits.max_risk_per_trade_pct * size_multiplier
    qty_by_risk = math.floor(risk_dollars / stop_distance)

    max_notional = equity * limits.max_position_pct * size_multiplier
    qty_by_exposure = math.floor(max_notional / entry_price)

    qty = min(qty_by_risk, qty_by_exposure)
    if qty < 1:
        return None

    take_profit_distance = atr * limits.take_profit_atr_multiple
    if side == "buy":
        stop_price = entry_price - stop_distance
        take_profit_price = entry_price + take_profit_distance
    else:
        stop_price = entry_price + stop_distance
        take_profit_price = entry_price - take_profit_distance

    if stop_price <= 0 or take_profit_price <= 0:
        return None

    return PositionPlan(
        symbol=symbol,
        side=side,
        qty=qty,
        entry_price=entry_price,
        stop_price=round(stop_price, 2),
        take_profit_price=round(take_profit_price, 2),
        risk_amount=qty * stop_distance,
        notional=qty * entry_price,
    )
