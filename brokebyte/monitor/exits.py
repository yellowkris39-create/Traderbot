"""Pure exit-management decisions for open positions (Phase 1 fix).

The news bot previously had NO exit backstop: a position could only close if
its bracket stop or take-profit child filled. Anything that drifted sideways
stayed open forever, which is why the paper account showed 0 closed trades.

This module adds two deterministic rules, expressed as a pure function so
they unit-test without a broker:

  1. Break-even stop: once price reaches +1R (one unit of initial risk),
     move the stop up to the entry price so the trade can't turn into a loss.
  2. Hard time-stop: if the position has been open `max_holding_days` trading
     days, close it at market regardless of price.

`decide_exit` returns an ExitAction describing WHAT to do; the broker wiring
(brokebyte.monitor.exit_manager) is responsible for executing it. Keeping the
decision pure means the risky part (live order changes) is thin and the logic
is fully covered by tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

# Action kinds
NONE = "none"
MOVE_BREAKEVEN = "move_breakeven"
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
) -> ExitAction:
    """Return the exit action for one open position.

    Time-stop takes precedence over the break-even move. The break-even move
    only fires once (when the live stop is still worse than entry), so calling
    this every reconcile cycle is idempotent.
    """
    # --- 1. Time-stop (highest priority) ---
    if trading_days_held(opened_at, now) >= max_holding_days:
        return ExitAction(
            kind=CLOSE_TIME_STOP,
            reason=f"time-stop: held >= {max_holding_days} trading days",
        )

    # --- 2. Break-even stop ---
    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share <= 0:
        return ExitAction(kind=NONE, reason="degenerate risk (entry == stop)")

    if side == "buy":
        reached_1r = current_price >= entry_price + breakeven_at_r * risk_per_share
        stop_below_entry = current_stop_price < entry_price
        if reached_1r and stop_below_entry:
            return ExitAction(
                kind=MOVE_BREAKEVEN,
                reason="reached +1R: move stop to break-even",
                new_stop_price=round(entry_price, 2),
            )
    elif side == "sell":
        reached_1r = current_price <= entry_price - breakeven_at_r * risk_per_share
        stop_above_entry = current_stop_price > entry_price
        if reached_1r and stop_above_entry:
            return ExitAction(
                kind=MOVE_BREAKEVEN,
                reason="reached +1R: move stop to break-even",
                new_stop_price=round(entry_price, 2),
            )
    else:
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

    return ExitAction(kind=NONE)
