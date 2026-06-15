"""Builds broker-side bracket orders from a sized PositionPlan.

Guardrail #9: stops live on the broker (native bracket order), not in the
bot's memory, so they fire even if the bot crashes or disconnects.
"""

from __future__ import annotations

from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

from brokebyte.risk.sizing import PositionPlan


def build_bracket_order(plan: PositionPlan) -> MarketOrderRequest:
    return MarketOrderRequest(
        symbol=plan.symbol,
        qty=plan.qty,
        side=OrderSide.BUY if plan.side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=plan.take_profit_price),
        stop_loss=StopLossRequest(stop_price=plan.stop_price),
    )
