"""Pure exit-management decisions for open positions.

The news bot previously had NO exit backstop: a position could only close if
its bracket stop or take-profit child filled. Anything that drifted sideways
stayed open forever (the historical "0 closed trades" bug).

`decide_exit` adds three deterministic rules, expressed as a pure function so
they unit-test without a broker:

  1. Hard time-stop: if open `max_holding_days` trading days, close at market.
  2. Break-even stop: at +1R, raise the stop to entry (can't turn into a loss).
  3. Trailing stop: at +1.5R and beyond, trail the stop to `trail_pct` below
     the current price, ratcheting UP only (never lowered, never below entry).

Break-even and trailing both surface as a MOVE_BREAKEVEN action carrying the
new stop price (the broker wiring treats any stop-raise identically); the
`reason` string distinguishes them. The move only fires when the proposed
stop is strictly better than the live stop, so calling this every cycle is
idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

# Action kinds
NONE = "none"
MOVE_BREAKEVEN = "move_breakeven"   # any upward stop move (break-even or trailing)
CLOSE_TIME_STOP = "close_time_stop"


@dataclass(frozen=True)
class ExitAction:
    kind: str                       # NONE | MOVE_BREAKEVEN | CLOSE_TIME_STOP
    reason: str = ""
    new_stop_price: float | None = None   # set when kind == MOVE_BREAKEVEN


def trading_days_held(opened_at: datetime, now: datetime) -> int:
    """Whole weekday (Mon-Fri) count between two datetimes. Approximates
    'trading days' without an exchange holiday calendar — documented limitation;
    a holiday-aware calendar can be swapped in later without changing callers."""
    start = np.datetime64(opened_at.date())
    end = np.datetime64(now.date())
    return int(np.busday_count(start, end))


def _target_stop(
    side: str,
    entry_price: float,
    risk_per_share: float,
    current_price: float,
    breakeven_at_r: float,
    trail_at_r: float,
    trail_pct: float,
) -> tuple[float, str] | None:
    """Return (proposed_stop, reason) for the current price, or None if price
    hasn't reached the break-even threshold yet. Trailing never sets a stop
    worse than break-even (entry)."""
    if side == "buy":
        if current_price >= entry_price + trail_at_r * risk_per_share:
            trailed = round(current_price * (1 - trail_pct), 2)
            return max(entry_price, trailed), f"trailing stop ({trail_pct:.0%} below price, >={trail_at_r}R)"
        if current_price >= entry_price + breakeven_at_r * risk_per_share:
            return entry_price, "reached +1R: move stop to break-even"
        return None
    else:  # sell / short
        if current_price <= entry_price - trail_at_r * risk_per_share:
            trailed = round(current_price * (1 + trail_pct), 2)
            return min(entry_price, trailed), f"trailing stop ({trail_pct:.0%} above price, >={trail_at_r}R)"
        if current_price <= entry_price - breakeven_at_r * risk_per_share:
            return entry_price, "reached +1R: move stop to break-even"
        return None


def decide_exit(
    *,
    side: str,                  # "buy" (long) or "sell" (short)
    entry_price: float,
    stop_price: float,          # ORIGINAL planned stop (defines 1R)
    current_stop_price: float,  # the stop currently live at the broker
    current_price: float,
    opened_at: datetime,
    now: datetime,
    max_holding_days: int = 10,
    breakeven_at_r: float = 1.0,
    trail_at_r: float = 1.5,
    trail_pct: float = 0.02,
) -> ExitAction:
    """Return the exit action for one open position. Time-stop takes precedence
    over any stop move."""
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

    # --- 1. Time-stop (highest priority) ---
    if trading_days_held(opened_at, now) >= max_holding_days:
        return ExitAction(
            kind=CLOSE_TIME_STOP,
            reason=f"time-stop: held >= {max_holding_days} trading days",
        )

    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share <= 0:
        return ExitAction(kind=NONE, reason="degenerate risk (entry == stop)")

    # --- 2/3. Break-even / trailing stop raise ---
    target = _target_stop(side, entry_price, risk_per_share, current_price,
                          breakeven_at_r, trail_at_r, trail_pct)
    if target is not None:
        proposed, reason = target
        better = proposed > current_stop_price if side == "buy" else proposed < current_stop_price
        if better:
            return ExitAction(kind=MOVE_BREAKEVEN, reason=reason,
                              new_stop_price=round(proposed, 2))

    return ExitAction(kind=NONE)
