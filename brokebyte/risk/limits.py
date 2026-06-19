"""Risk floors — human-set constants.

Guardrail: these values are read from the environment at startup and never
written by any code path. No part of the bot, the calibration layer (Module
7), or an AI review step (Module 12) may edit these values or the env vars
that back them. Changing a risk floor is a deliberate human action: edit the
environment and restart.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    # --- Position sizing ---
    max_risk_per_trade_pct: float = 0.005  # 0.5% of equity risked (to stop) per trade
    max_position_pct: float = 0.10  # max 10% of equity notional in one symbol
    stop_loss_atr_multiple: float = 2.0
    take_profit_atr_multiple: float = 4.0

    # --- Portfolio limits ---
    max_open_positions: int = 5
    max_daily_loss_pct: float = 0.02  # 2% of equity daily loss -> halt new entries

    # --- Liquidity guard (Module 10) ---
    min_price: float = 5.0
    max_spread_pct: float = 0.005  # 0.5%
    fill_deviation_tolerance_pct: float = 0.01  # 1%

    # --- Circuit breakers (Module 11) ---
    max_trades_per_hour: int = 6
    max_consecutive_errors: int = 3

    # --- LLM confidence floor ---
    min_confidence: float = 0.60  # reject verdicts below this threshold


def _float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    return default if value is None or value == "" else float(value)


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if value is None or value == "" else int(value)


def load_risk_limits() -> RiskLimits:
    defaults = RiskLimits()
    return RiskLimits(
        max_risk_per_trade_pct=_float_env("RISK_MAX_RISK_PER_TRADE_PCT", defaults.max_risk_per_trade_pct),
        max_position_pct=_float_env("RISK_MAX_POSITION_PCT", defaults.max_position_pct),
        stop_loss_atr_multiple=_float_env("RISK_STOP_LOSS_ATR_MULTIPLE", defaults.stop_loss_atr_multiple),
        take_profit_atr_multiple=_float_env("RISK_TAKE_PROFIT_ATR_MULTIPLE", defaults.take_profit_atr_multiple),
        max_open_positions=_int_env("RISK_MAX_OPEN_POSITIONS", defaults.max_open_positions),
        max_daily_loss_pct=_float_env("RISK_MAX_DAILY_LOSS_PCT", defaults.max_daily_loss_pct),
        min_price=_float_env("RISK_MIN_PRICE", defaults.min_price),
        max_spread_pct=_float_env("RISK_MAX_SPREAD_PCT", defaults.max_spread_pct),
        fill_deviation_tolerance_pct=_float_env(
            "RISK_FILL_DEVIATION_TOLERANCE_PCT", defaults.fill_deviation_tolerance_pct
        ),
        max_trades_per_hour=_int_env("RISK_MAX_TRADES_PER_HOUR", defaults.max_trades_per_hour),
        max_consecutive_errors=_int_env("RISK_MAX_CONSECUTIVE_ERRORS", defaults.max_consecutive_errors),
        min_confidence=_float_env("RISK_MIN_CONFIDENCE", defaults.min_confidence),
    )
