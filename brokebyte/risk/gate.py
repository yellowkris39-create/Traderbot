"""Risk Gate (Module 4) — Phase 2.

Orchestrates the risk/guard checks into a single entry decision. Default
action is HOLD: a PositionPlan is returned only if every check passes, in
order from cheapest/most-likely-to-reject to most expensive:

1. Base verdict checks (material, symbol, direction, not already priced in)
2. Guard 8  — injection-pattern scan + symbol grounding
3. Guard 11 — circuit breakers (consecutive errors, trade rate)
4. Module 4 — portfolio limits (daily-loss halt, max open positions)
5. Guard 10 — liquidity (price floor, spread)
6. Guard 9  — regime size multiplier
7. Module 4 — volatility-based sizing + exposure cap

A daily-loss-halt trip also asks the caller to fire the kill switch
(flatten + cancel) via `kill_switch_reason` — the one anomaly severe enough
to de-risk the whole book rather than just HOLD new entries. Existing
positions are otherwise protected by their broker-side bracket stops
(guardrail #9) regardless of bot/circuit-breaker state.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from brokebyte.analysis.indicators import atr
from brokebyte.common import Quote
from brokebyte.guards.circuit_breakers import CircuitBreaker
from brokebyte.guards.grounding import check_injection_patterns, check_symbol_grounding
from brokebyte.guards.liquidity import check_price_floor, check_spread
from brokebyte.guards.regime import classify_regime
from brokebyte.ingestion.events import NewsEvent
from brokebyte.llm.provider import Direction, LLMVerdict
from brokebyte.risk.limits import RiskLimits
from brokebyte.risk.portfolio import PortfolioState, check_daily_loss_halt, check_exposure, check_max_open_positions
from brokebyte.risk.sizing import PositionPlan, size_position


@dataclass(frozen=True)
class GateDecision:
    plan: PositionPlan | None
    reason: str
    kill_switch_reason: str | None = None

    @property
    def action(self) -> str:
        return "ENTER" if self.plan is not None else "HOLD"


def _hold(reason: str, kill_switch_reason: str | None = None) -> GateDecision:
    return GateDecision(plan=None, reason=reason, kill_switch_reason=kill_switch_reason)


def evaluate(
    verdict: LLMVerdict,
    event: NewsEvent,
    bars: pd.DataFrame,
    quote: Quote,
    portfolio: PortfolioState,
    limits: RiskLimits,
    circuit_breaker: CircuitBreaker,
) -> GateDecision:
    # 1. Base verdict checks --------------------------------------------------
    if not verdict.material:
        return _hold("verdict not material")
    if verdict.symbol is None:
        return _hold("verdict names no symbol")
    if verdict.direction == Direction.NONE:
        return _hold("verdict direction is none")
    if verdict.is_already_priced_in:
        return _hold("already priced in")

    # 2. Guard 8 — injection / hallucination ----------------------------------
    injection_check = check_injection_patterns(event)
    if not injection_check.ok:
        return _hold(f"guard 8 (injection): {injection_check.reason}")

    grounding_check = check_symbol_grounding(verdict, event)
    if not grounding_check.ok:
        return _hold(f"guard 8 (grounding): {grounding_check.reason}")

    # 3. Guard 11 — circuit breakers -------------------------------------------
    errors_check = circuit_breaker.check_consecutive_errors(limits)
    if not errors_check.ok:
        return _hold(f"guard 11 (circuit breaker): {errors_check.reason}")

    rate_check = circuit_breaker.check_trade_rate(limits)
    if not rate_check.ok:
        return _hold(f"guard 11 (circuit breaker): {rate_check.reason}")

    # 4. Portfolio limits (Module 4) -------------------------------------------
    daily_loss_check = check_daily_loss_halt(portfolio, limits)
    if not daily_loss_check.ok:
        return _hold(f"portfolio: {daily_loss_check.reason}", kill_switch_reason=daily_loss_check.reason)

    positions_check = check_max_open_positions(portfolio, verdict.symbol, limits)
    if not positions_check.ok:
        return _hold(f"portfolio: {positions_check.reason}")

    # 5. Guard 10 — liquidity / spread -----------------------------------------
    price_check = check_price_floor(quote.mid, limits)
    if not price_check.ok:
        return _hold(f"guard 10 (liquidity): {price_check.reason}")

    spread_check = check_spread(quote, limits)
    if not spread_check.ok:
        return _hold(f"guard 10 (liquidity): {spread_check.reason}")

    # 6. Guard 9 — regime --------------------------------------------------------
    regime = classify_regime(bars)

    # 7. Module 4 — volatility-based sizing + exposure cap ----------------------
    try:
        atr_value = atr(bars)
    except ValueError:
        return _hold("not enough bar history to compute ATR")

    side = "buy" if verdict.direction == Direction.LONG else "sell"
    plan = size_position(
        symbol=verdict.symbol,
        side=side,
        entry_price=quote.mid,
        atr=atr_value,
        equity=portfolio.equity,
        limits=limits,
        size_multiplier=regime.size_multiplier,
    )
    if plan is None:
        return _hold("position size rounds to zero")

    exposure_check = check_exposure(portfolio, plan, limits)
    if not exposure_check.ok:
        return _hold(f"portfolio: {exposure_check.reason}")

    return GateDecision(
        plan=plan,
        reason=f"entry approved (regime={regime.trend.value}, size_multiplier={regime.size_multiplier})",
    )
