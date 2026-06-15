"""Thin wrapper around alpaca-py's TradingClient.

Paper vs. live is driven entirely by Config.is_paper (brokebyte.config) —
this module never hardcodes paper=False.
"""

from __future__ import annotations

from alpaca.trading.client import TradingClient
from alpaca.trading.models import Order

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
        }

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
