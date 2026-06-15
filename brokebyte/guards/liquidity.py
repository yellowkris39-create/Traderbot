"""Guard 10 — Liquidity / Spread Guard.

Refuses illiquid names (price floor) or wide spreads before an order is
sized/placed, and flags fills that deviate too far from the expected price
— a signal of stale data or thin liquidity rather than a normal fill.
"""

from __future__ import annotations

from brokebyte.common import CheckResult, Quote
from brokebyte.risk.limits import RiskLimits


def check_price_floor(price: float, limits: RiskLimits) -> CheckResult:
    if price < limits.min_price:
        return CheckResult(False, f"price {price:.2f} below minimum {limits.min_price:.2f}")
    return CheckResult(True)


def check_spread(quote: Quote, limits: RiskLimits) -> CheckResult:
    if quote.spread_pct > limits.max_spread_pct:
        return CheckResult(
            False,
            f"spread {quote.spread_pct:.4%} exceeds max {limits.max_spread_pct:.4%}",
        )
    return CheckResult(True)


def check_fill_deviation(expected_price: float, fill_price: float, limits: RiskLimits) -> CheckResult:
    """Flag a fill that deviates beyond tolerance from the expected price."""
    if expected_price <= 0:
        return CheckResult(True)

    deviation_pct = abs(fill_price - expected_price) / expected_price
    if deviation_pct > limits.fill_deviation_tolerance_pct:
        return CheckResult(
            False,
            f"fill {fill_price:.2f} deviates {deviation_pct:.4%} from expected {expected_price:.2f} "
            f"(tolerance {limits.fill_deviation_tolerance_pct:.4%})",
        )
    return CheckResult(True)
