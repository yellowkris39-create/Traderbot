"""Portfolio-level risk checks: exposure per name, max open positions, and
the daily-loss halt (Module 4 portfolio limits).

`PortfolioState` is built from plain dicts (see `from_account_and_positions`)
so these checks are testable without any broker/Alpaca dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from brokebyte.common import CheckResult
from brokebyte.risk.limits import RiskLimits
from brokebyte.risk.sizing import PositionPlan


@dataclass(frozen=True)
class PositionInfo:
    symbol: str
    qty: float
    market_value: float


@dataclass(frozen=True)
class PortfolioState:
    equity: float
    cash: float
    last_equity: float
    positions: dict[str, PositionInfo] = field(default_factory=dict)

    @property
    def daily_pnl(self) -> float:
        return self.equity - self.last_equity

    @property
    def daily_pnl_pct(self) -> float:
        if self.last_equity == 0:
            return 0.0
        return self.daily_pnl / self.last_equity


def from_account_and_positions(account: dict, positions: list[dict]) -> PortfolioState:
    """Build a PortfolioState from the simple dicts returned by
    Broker.get_account_summary() / Broker.get_positions()."""
    return PortfolioState(
        equity=float(account["equity"]),
        cash=float(account["cash"]),
        last_equity=float(account["last_equity"]),
        positions={
            p["symbol"]: PositionInfo(symbol=p["symbol"], qty=float(p["qty"]), market_value=float(p["market_value"]))
            for p in positions
        },
    )


def check_daily_loss_halt(portfolio: PortfolioState, limits: RiskLimits) -> CheckResult:
    """HOLD all new entries if today's loss has reached the halt threshold."""
    if portfolio.last_equity <= 0:
        return CheckResult(True)

    loss_pct = -portfolio.daily_pnl_pct  # positive when equity is down
    if loss_pct >= limits.max_daily_loss_pct:
        return CheckResult(
            False,
            f"daily loss {loss_pct:.2%} >= halt limit {limits.max_daily_loss_pct:.2%}",
        )
    return CheckResult(True)


def check_max_open_positions(portfolio: PortfolioState, symbol: str, limits: RiskLimits) -> CheckResult:
    """Reject new names once max_open_positions is reached. Adding to an
    existing position doesn't increase the open-position count."""
    if symbol in portfolio.positions:
        return CheckResult(True)
    if len(portfolio.positions) >= limits.max_open_positions:
        return CheckResult(False, f"max open positions ({limits.max_open_positions}) reached")
    return CheckResult(True)


def check_exposure(portfolio: PortfolioState, plan: PositionPlan, limits: RiskLimits) -> CheckResult:
    """Reject if existing + new notional in this symbol would exceed
    max_position_pct of equity."""
    existing = portfolio.positions.get(plan.symbol)
    existing_value = abs(existing.market_value) if existing else 0.0
    total_exposure = existing_value + plan.notional
    max_allowed = portfolio.equity * limits.max_position_pct

    if total_exposure > max_allowed:
        return CheckResult(
            False,
            f"exposure {total_exposure:.2f} > max {max_allowed:.2f} for {plan.symbol}",
        )
    return CheckResult(True)
