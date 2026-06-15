"""Track A mechanical backtest engine (SPEC.md Sec 5 / Sec 6 build order step 5).

Track A validates "the parts that don't involve LLM foresight": sizing,
stops, execution, and cost/slippage mechanics. It needs *some* entry signal
to drive those mechanics without invoking the LLM, so it reuses Module 3's
purely rule-based confluence check as a deterministic trend-following
baseline:

  - classify_regime(bars[:i+1]) gives a Trend (UP / DOWN / CHOPPY) using only
    bars known up to and including day i.
  - Trend.UP -> synthetic LONG verdict, Trend.DOWN -> synthetic SHORT
    verdict, Trend.CHOPPY -> no trade (CHOPPY never has confluence under
    Module 3's design).
  - That synthetic verdict is run through the *real* check_confluence and
    size_position, so support/resistance blocking, ATR-based stops, and
    regime-scaled position sizing are exercised exactly as in live trading.

No-lookahead discipline: at bar i, only bars[:i+1] is visible for regime and
indicator computation. Entries fill at bar i+1's open, with slippage applied
against the trader. Stop and take-profit levels are then checked against
subsequent bars' high/low; if both could be hit on the same bar, the stop is
assumed to hit first (conservative).

Scope: single-symbol only, one open position at a time. size_position()'s
own exposure cap (qty_by_exposure) is exercised, but the cross-symbol
portfolio checks in risk/portfolio.py (exposure across symbols, max open
positions, daily loss halt) are out of scope for a single-symbol backtest
and are not simulated here.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from brokebyte.analysis.indicators import atr
from brokebyte.backtest.costs import CostModel
from brokebyte.fusion.context import check_confluence
from brokebyte.guards.regime import Trend, classify_regime
from brokebyte.llm.provider import Direction, LLMVerdict, TimeHorizon
from brokebyte.risk.limits import RiskLimits
from brokebyte.risk.sizing import PositionPlan, size_position

# classify_regime's default slow_period; below this it fails safe to CHOPPY,
# which never has confluence, so no trade can be generated anyway.
MIN_LOOKBACK_BARS = 50


@dataclass(frozen=True)
class BacktestTrade:
    symbol: str
    side: str  # "buy" | "sell" (entry side)
    entry_index: int
    entry_label: object  # bars["timestamp"] value if present, else bar index
    entry_price: float
    exit_index: int
    exit_label: object
    exit_price: float
    exit_reason: str  # "stop" | "take_profit" | "end_of_data"
    qty: int
    pnl: float
    r_multiple: float


@dataclass(frozen=True)
class BacktestResult:
    trades: list[BacktestTrade]
    equity_curve: list[float]


def bar_label(bars: pd.DataFrame, i: int) -> object:
    """The value used to identify bar `i` in reports: its timestamp if
    `bars` has a "timestamp" column, otherwise its integer position."""
    if "timestamp" in bars.columns:
        return bars["timestamp"].iloc[i]
    return i


def _synthetic_verdict(symbol: str, direction: Direction) -> LLMVerdict:
    return LLMVerdict(
        material=True,
        symbol=symbol,
        direction=direction,
        confidence=1.0,
        time_horizon=TimeHorizon.SWING,
        reasoning="Track A mechanical backtest: trend-following baseline from classify_regime",
        is_already_priced_in=False,
    )


def _find_exit(bars: pd.DataFrame, entry_index: int, direction: Direction, plan: PositionPlan) -> tuple[int, float, str]:
    """Scan bars after entry for a stop or take-profit hit. If both could be
    hit on the same bar, assume the stop hit first (conservative). Falls
    back to the last bar's close if neither is hit before the data ends."""
    n = len(bars)
    for j in range(entry_index + 1, n):
        bar = bars.iloc[j]
        if direction == Direction.LONG:
            stop_hit = bar["low"] <= plan.stop_price
            tp_hit = bar["high"] >= plan.take_profit_price
        else:
            stop_hit = bar["high"] >= plan.stop_price
            tp_hit = bar["low"] <= plan.take_profit_price

        if stop_hit:
            return j, plan.stop_price, "stop"
        if tp_hit:
            return j, plan.take_profit_price, "take_profit"

    last = n - 1
    return last, float(bars["close"].iloc[last]), "end_of_data"


def run_backtest(
    bars: pd.DataFrame,
    symbol: str,
    limits: RiskLimits,
    cost_model: CostModel,
    initial_equity: float = 100_000.0,
) -> BacktestResult:
    """Walk `bars` (oldest-first, columns open/high/low/close required)
    forward one trade at a time, generating signals via classify_regime +
    check_confluence and sizing/exiting via the real risk/sizing logic.
    Returns the closed trades and an equity curve sampled after each trade."""
    n = len(bars)
    equity = initial_equity
    equity_curve = [equity]
    trades: list[BacktestTrade] = []

    i = MIN_LOOKBACK_BARS - 1
    while i < n - 1:
        window = bars.iloc[: i + 1]
        regime = classify_regime(window)

        if regime.trend == Trend.UP:
            direction = Direction.LONG
        elif regime.trend == Trend.DOWN:
            direction = Direction.SHORT
        else:
            i += 1
            continue

        verdict = _synthetic_verdict(symbol, direction)
        price = float(window["close"].iloc[-1])
        check_result, _proposal = check_confluence(verdict, window, price)
        if not check_result.ok:
            i += 1
            continue

        entry_index = i + 1
        entry_side = "buy" if direction == Direction.LONG else "sell"
        raw_entry_price = float(bars["open"].iloc[entry_index])
        entry_price = cost_model.apply_slippage(raw_entry_price, entry_side)

        plan = size_position(
            symbol=symbol,
            side=entry_side,
            entry_price=entry_price,
            atr=atr(window),
            equity=equity,
            limits=limits,
            size_multiplier=regime.size_multiplier,
        )
        if plan is None:
            i += 1
            continue

        exit_index, exit_price_raw, exit_reason = _find_exit(bars, entry_index, direction, plan)

        exit_side = "sell" if direction == Direction.LONG else "buy"
        exit_price = cost_model.apply_slippage(exit_price_raw, exit_side)

        entry_fees = cost_model.fees(entry_side, entry_price * plan.qty, plan.qty)
        exit_fees = cost_model.fees(exit_side, exit_price * plan.qty, plan.qty)

        if direction == Direction.LONG:
            gross_pnl = (exit_price - entry_price) * plan.qty
        else:
            gross_pnl = (entry_price - exit_price) * plan.qty

        pnl = gross_pnl - entry_fees - exit_fees
        equity += pnl
        equity_curve.append(equity)

        trades.append(
            BacktestTrade(
                symbol=symbol,
                side=entry_side,
                entry_index=entry_index,
                entry_label=bar_label(bars, entry_index),
                entry_price=entry_price,
                exit_index=exit_index,
                exit_label=bar_label(bars, exit_index),
                exit_price=exit_price,
                exit_reason=exit_reason,
                qty=plan.qty,
                pnl=pnl,
                r_multiple=pnl / plan.risk_amount,
            )
        )

        i = exit_index + 1

    return BacktestResult(trades=trades, equity_curve=equity_curve)
