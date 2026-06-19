"""Thin wrapper around alpaca-py's TradingClient.

Paper vs. live is driven entirely by Config.is_paper (brokebyte.config) —
this module never hardcodes paper=False.
"""

from __future__ import annotations

from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.common.enums import Sort
from alpaca.trading.enums import OrderSide, OrderStatus, QueryOrderStatus
from alpaca.trading.models import Order
from alpaca.trading.requests import GetOrdersRequest

from brokebyte.common import FilledOrder
from brokebyte.config import Config
from brokebyte.risk.kill_switch import KillSwitchResult, execute_kill_switch
from brokebyte.risk.orders import build_bracket_order
from brokebyte.risk.sizing import PositionPlan


class Broker:
    def __init__(self, config: Config) -> None:
        self._client = TradingClient(
            api_key=config.alpaca.api_key,
            secret_key=config.alpaca.secret_key,
            paper=config.is_paper,
        )
        self.is_paper = config.is_paper

    def get_account_summary(self) -> dict:
        account = self._client.get_account()
        return {
            "account_id": str(account.id),
            "status": str(account.status),
            "cash": account.cash,
            "equity": account.equity,
            "last_equity": account.last_equity,
            "portfolio_value": account.portfolio_value,
            "buying_power": account.buying_power,
            "shorting_enabled": bool(getattr(account, "shorting_enabled", True)),
        }

    def is_market_open(self) -> bool:
        """Return True if the US stock market is currently open for trading."""
        clock = self._client.get_clock()
        return bool(clock.is_open)

    def get_positions(self) -> list[dict]:
        positions = self._client.get_all_positions()
        return [
            {"symbol": p.symbol, "qty": p.qty, "market_value": p.market_value or "0"}
            for p in positions
        ]

    def submit_bracket_order(self, plan: PositionPlan) -> Order:
        return self._client.submit_order(build_bracket_order(plan))

    def kill_switch(self, reason: str) -> KillSwitchResult:
        return execute_kill_switch(self._client, reason)

    def get_position_symbols(self) -> set[str]:
        """Symbols of currently open positions."""
        return {p["symbol"] for p in self.get_positions()}

    def get_order_exit_fill(self, order_id: str) -> FilledOrder | None:
        """Return the filled exit leg of a bracket order, or None if not yet filled."""
        try:
            order = self._client.get_order_by_id(order_id, nested=True)
        except Exception:
            return None

        if not order.legs:
            return None

        entry_side = order.side
        exit_side = OrderSide.SELL if entry_side == OrderSide.BUY else OrderSide.BUY

        for leg in order.legs:
            if (
                leg.side == exit_side
                and leg.status == OrderStatus.FILLED
                and leg.filled_avg_price is not None
            ):
                return FilledOrder(
                    fill_price=float(leg.filled_avg_price),
                    filled_at=leg.filled_at,
                )
        return None

    def get_filled_exit_order(
        self, symbol: str, after: datetime, plan_side: str
    ) -> FilledOrder | None:
        """Find the most recent filled exit order for `symbol` placed after `after`.

        Returns None if no filled exit order is found (position may still be open
        or the fill hasn't appeared in order history yet).
        """
        exit_side = OrderSide.SELL if plan_side == "buy" else OrderSide.BUY

        request = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            symbols=[symbol],
            after=after,
            direction=Sort.DESC,
            limit=20,
            nested=True,
        )
        orders = self._client.get_orders(filter=request)

        for order in orders:
            # Check the order itself (simple or bracket exit leg listed independently)
            if (
                order.side == exit_side
                and order.status == OrderStatus.FILLED
                and order.filled_avg_price is not None
            ):
                return FilledOrder(
                    fill_price=float(order.filled_avg_price),
                    filled_at=order.filled_at,
                )
            # Check nested legs (bracket order with legs attached)
            if order.legs:
                for leg in order.legs:
                    if (
                        leg.side == exit_side
                        and leg.status == OrderStatus.FILLED
                        and leg.filled_avg_price is not None
                    ):
                        return FilledOrder(
                            fill_price=float(leg.filled_avg_price),
                            filled_at=leg.filled_at,
                        )
        return None
