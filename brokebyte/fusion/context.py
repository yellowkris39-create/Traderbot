"""Module 3 — Context Fusion ("understands the graphs").

Fuses the LLM verdict with technical context (trend regime + proximity to
recent support/resistance) into a TradeProposal, and checks confluence:
the verdict's direction must agree with the trend, and the entry must not
be immediately against a nearby support/resistance level ("same headline,
different chart -> different trade"). The news verdict alone is never
sufficient - this stage can only narrow ENTER down to HOLD, in keeping
with the "trade less, not more" guardrail philosophy.

Sizing itself is untouched: TradeProposal.regime.size_multiplier flows into
risk/sizing.py exactly as Guard 9's Regime did before this module existed.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from brokebyte.analysis.indicators import support_resistance
from brokebyte.common import CheckResult
from brokebyte.guards.regime import Regime, Trend, classify_regime
from brokebyte.llm.provider import Direction, LLMVerdict

# Within this fraction of a level counts as "at" it for confluence purposes.
NEAR_LEVEL_PCT = 0.005


@dataclass(frozen=True)
class TradeProposal:
    verdict: LLMVerdict
    regime: Regime
    support: float
    resistance: float


def _trend_agrees(direction: Direction, trend: Trend) -> bool:
    if direction == Direction.LONG:
        return trend == Trend.UP
    if direction == Direction.SHORT:
        return trend == Trend.DOWN
    return False


def _blocked_by_level(direction: Direction, price: float, support: float, resistance: float) -> CheckResult:
    """A long heading straight into overhead resistance, or a short heading
    straight into underlying support, is a worse setup than the same
    verdict in open space - "different chart, different trade"."""
    if direction == Direction.LONG and price < resistance and (resistance - price) / price < NEAR_LEVEL_PCT:
        return CheckResult(False, f"price {price:.2f} within {NEAR_LEVEL_PCT:.1%} of resistance {resistance:.2f}")
    if direction == Direction.SHORT and price > support and (price - support) / price < NEAR_LEVEL_PCT:
        return CheckResult(False, f"price {price:.2f} within {NEAR_LEVEL_PCT:.1%} of support {support:.2f}")
    return CheckResult(True)


def check_confluence(verdict: LLMVerdict, bars: pd.DataFrame, price: float) -> tuple[CheckResult, TradeProposal]:
    """Confluence check + the fused TradeProposal. The proposal is returned
    even on failure so the regime/levels are available for logging."""
    regime = classify_regime(bars)
    try:
        support, resistance = support_resistance(bars)
    except ValueError:
        support, resistance = price, price

    proposal = TradeProposal(verdict=verdict, regime=regime, support=support, resistance=resistance)

    if not _trend_agrees(verdict.direction, regime.trend):
        reason = f"no confluence: verdict={verdict.direction.value} but trend={regime.trend.value}"
        return CheckResult(False, reason), proposal

    level_check = _blocked_by_level(verdict.direction, price, support, resistance)
    if not level_check.ok:
        return CheckResult(False, f"no confluence: {level_check.reason}"), proposal

    return CheckResult(True), proposal
