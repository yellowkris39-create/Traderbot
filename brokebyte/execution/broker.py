"""Thin wrapper around alpaca-py's TradingClient.

Paper vs. live is driven entirely by Config.is_paper (brokebyte.config) —
this module never hardcodes paper=False.
"""

from __future__ import annotations

from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.common.enums import Sort
from alpaca.trading.enums import OrderSide, OrderStatus, OrderType, QueryOrderStatus
from alpaca.trading.models import Order
from alpaca.trading.requests import GetOrderByIdRequest, GetOrdersRequest, ReplaceOrderRequest

_NESTED = GetOrderByIdRequest(nested=True)

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
            order = self._client.get_order_by_id(order_id, filter=_NESTED)
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
            if (
                order.side == exit_side
                and order.status == OrderStatus.FILLED
                and order.filled_avg_price is not None
            ):
                return FilledOrder(
                    fill_price=float(order.filled_avg_price),
                    filled_at=order.filled_at,
                )
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

    # -- Active exit management (used by brokebyte.monitor.exit_manager) -------
    # INTEGRATION NOTE: verify these four against the paper account before
    # relying on them in production (esp. how flatten interacts with the open
    # bracket legs). Method names confirmed against alpaca-py.

    def get_current_price(self, symbol: str) -> float | None:
        """Latest price for an OPEN position, or None if no position exists."""
        try:
            pos = self._client.get_open_position(symbol)
        except Exception:
            return None
        if pos is None or pos.current_price is None:
            return None
        return float(pos.current_price)

    def get_open_stop(self, order_id: str) -> tuple[str, float] | None:
        """Return (stop_leg_id, stop_price) for the still-open stop leg of a
        bracket order, or None if there is no open stop leg."""
        try:
            order = self._client.get_order_by_id(order_id, filter=_NESTED)
        except Exception:
            return None
        legs = order.legs or []
        open_states = {OrderStatus.NEW, OrderStatus.ACCEPTED, OrderStatus.HELD,
                       OrderStatus.PENDING_NEW}
        for leg in legs:
            if (
                leg.order_type in (OrderType.STOP, OrderType.STOP_LIMIT)
                and leg.status in open_states
                and leg.stop_price is not None
            ):
                return str(leg.id), float(leg.stop_price)
        return None

    def replace_stop(self, stop_leg_id: str, new_stop_price: float) -> None:
        """Move an existing stop leg to a new stop price (break-even move)."""
        self._client.replace_order_by_id(
            stop_leg_id, ReplaceOrderRequest(stop_price=new_stop_price)
        )

    def flatten(self, symbol: str, order_id: str) -> FilledOrder | None:
        """Force-close a position at market for the time-stop. Cancels the
        bracket's open legs first so they don't fight the market close, then
        closes the position. Returns the close fill if available this cycle."""
        try:
            order = self._client.get_order_by_id(order_id, filter=_NESTED)
            for leg in (order.legs or []):
                if leg.status in {OrderStatus.NEW, OrderStatus.ACCEPTED,
                                  OrderStatus.HELD, OrderStatus.PENDING_NEW}:
                    try:
                        self._client.cancel_order_by_id(str(leg.id))
                    except Exception:
                        pass
        except Exception:
            pass

        close_order = self._client.close_position(symbol)
        if (
            getattr(close_order, "status", None) == OrderStatus.FILLED
            and getattr(close_order, "filled_avg_price", None) is not None
        ):
            return FilledOrder(
                fill_price=float(close_order.filled_avg_price),
                filled_at=close_order.filled_at,
            )
        return None
