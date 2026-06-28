"""£500-account swing-trade position sizing (Strategy v2).

Differs from the news bot's ATR sizer (brokebyte/risk/sizing.py): the stop
here is the trade's swing-low stop, risk is a fixed 1% of the account (£5),
and shares are FRACTIONAL (rounded down to `lot_decimals` places) because the
target brokers (Trading 212 / Freetrade) allow fractional trading.

Two caps apply and the smaller share count wins:
  1. Risk cap: loss if the stop hits must equal ~1% of the account.
  2. Exposure cap: notional must stay within `max_position_pct` of the account.

NOTE: the strategy's worked example (3.33 shares of a £50 stock) puts £166.50
into one position, which EXCEEDS the 20% (£100) cap it also states. The two
rules conflict; this sizer honours the exposure cap (the safer choice), so for
that example it returns 2.00 shares, not 3.33. Set max_position_pct high to
reproduce the raw risk-based number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class GbpTradePlan:
    shares: float
    entry_price: float
    stop_price: float
    take_profit_price: float
    risk_per_share: float
    risk_amount: float       # actual £ at risk for the final share count
    notional: float          # shares * entry
    exposure_capped: bool    # True if the 20% cap reduced the size


def _floor_to(x: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.floor(x * factor) / factor


def size_trade_gbp(
    entry_price: float,
    stop_price: float,
    *,
    account: float = 500.0,
    risk_pct: float = 0.01,
    reward_risk: float = 2.0,
    max_position_pct: float = 0.20,
    lot_decimals: int = 2,
) -> GbpTradePlan | None:
    """Return a sized long plan, or None if no valid (>0) size exists.

    Only long setups are sized (the swing strategy is long-only). `stop_price`
    must be below `entry_price`.
    """
    if entry_price <= 0 or stop_price <= 0 or stop_price >= entry_price:
        return None

    risk_per_share = entry_price - stop_price
    risk_amount_target = account * risk_pct

    shares_by_risk = _floor_to(risk_amount_target / risk_per_share, lot_decimals)
    max_notional = account * max_position_pct
    shares_by_exposure = _floor_to(max_notional / entry_price, lot_decimals)

    shares = min(shares_by_risk, shares_by_exposure)
    if shares <= 0:
        return None

    take_profit_price = entry_price + reward_risk * risk_per_share
    return GbpTradePlan(
        shares=shares,
        entry_price=round(entry_price, 4),
        stop_price=round(stop_price, 4),
        take_profit_price=round(take_profit_price, 4),
        risk_per_share=round(risk_per_share, 4),
        risk_amount=round(shares * risk_per_share, 2),
        notional=round(shares * entry_price, 2),
        exposure_capped=shares_by_exposure < shares_by_risk,
    )
