"""Small shared types used across risk/guards modules.

Kept dependency-free (no pandas, no alpaca) so risk/guard logic can be
unit-tested without any broker or market-data client.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CheckResult:
    """Generic pass/fail result with a human-readable reason for logging."""

    ok: bool
    reason: str | None = None


@dataclass(frozen=True)
class Quote:
    bid_price: float
    ask_price: float

    @property
    def mid(self) -> float:
        return (self.bid_price + self.ask_price) / 2

    @property
    def spread_pct(self) -> float:
        if self.mid <= 0:
            return float("inf")
        return (self.ask_price - self.bid_price) / self.mid
