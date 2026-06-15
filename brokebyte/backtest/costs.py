"""Transaction cost model for Track A mechanical backtests (SPEC.md Sec 5).

Free market-data feeds (IEX) give optimistic fills relative to what a real
order would experience on the full tape, so Track A always applies slippage
against the trader. Regulatory fee rates approximate current SEC Section 31
and FINRA Trading Activity Fee rates as of 2026 -- verify current pricing
before relying on these figures for anything beyond mechanical validation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    slippage_bps: float = 5.0
    sec_fee_rate: float = 0.0000278  # per dollar of sell proceeds
    finra_taf_per_share: float = 0.000166  # per share, sell side only
    finra_taf_cap: float = 8.30  # per-order cap

    def apply_slippage(self, price: float, side: str) -> float:
        """Return the fill price after slippage, always against the trader.

        Buys fill higher than the quoted price; sells fill lower.
        """
        factor = self.slippage_bps / 10_000.0
        if side == "buy":
            return price * (1 + factor)
        if side == "sell":
            return price * (1 - factor)
        raise ValueError(f"unknown side: {side!r}")

    def fees(self, side: str, notional: float, qty: int) -> float:
        """Return total regulatory fees for a fill.

        SEC Section 31 fees and the FINRA TAF apply only to sell-side
        transactions (exits from long positions and entries into shorts).
        """
        if side != "sell":
            return 0.0
        sec_fee = notional * self.sec_fee_rate
        taf = min(qty * self.finra_taf_per_share, self.finra_taf_cap)
        return sec_fee + taf
