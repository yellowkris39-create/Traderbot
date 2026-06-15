"""Thin wrapper around alpaca-py's TradingClient.

Paper vs. live is driven entirely by Config.is_paper (brokebyte.config) —
this module never hardcodes paper=False.
"""

from __future__ import annotations

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.models import Order
from alpaca.trading.requests import MarketOrderRequest

from brokebyte.config import Config
from brokebyte.risk.gate import OrderIntent


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
            "portfolio_value": account.portfolio_value,
            "buying_power": account.buying_power,
        }

    def submit_market_order(self, intent: OrderIntent) -> Order:
        order_data = MarketOrderRequest(
            symbol=intent.symbol,
            qty=intent.qty,
            side=OrderSide.BUY if intent.side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        return self._client.submit_order(order_data)
