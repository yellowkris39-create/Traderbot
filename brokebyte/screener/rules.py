"""The reconciled swing-trade ruleset (REASSESSMENT_AND_PLAN.md §3) as pure
predicates. Thresholds live here as named constants so they're easy to tune
and audit. Functions take already-computed metrics (not raw bars) to stay
pure and trivially testable; brokebyte.screener.screen wires the indicators
to these checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Universe / liquidity thresholds ---
MIN_PRICE = 5.0
MAX_PRICE = 1000.0  # raised from 200 (Kris, 2026-07-16): the $200 cap was a relic of
# the abandoned £500 whole-share plan and blocked 356/853 symbols nightly (42%, funnel
# 2026-07-15). Sizing still enforces affordability (whole shares + 20% exposure cap).
# The validated backtest had NO price filter, so raising this REDUCES config drift.
MIN_MARKET_CAP = 500_000_000.0   # £500M / $500M
MIN_AVG_VOLUME = 1_000_000.0     # locked decision: 1M shares
MAX_BETA = 1.5
EARNINGS_BLACKOUT_DAYS = 7

# --- Setup thresholds ---
PULLBACK_MIN_PCT = 0.03          # 3%
PULLBACK_MAX_PCT = 0.10          # 10%
RSI_SETUP_LOW = 40.0
RSI_SETUP_HIGH = 60.0
RSI_TRIGGER_FLOOR = 40.0


@dataclass
class FilterResult:
    passed: bool
    failures: list[str] = field(default_factory=list)


def check_universe(
    price: float,
    market_cap: float,
    avg_volume: float,
    beta: float,
    days_to_earnings: float | None,
) -> FilterResult:
    """Liquidity / size / earnings-blackout gate. `days_to_earnings` may be
    None when no earnings date is known (treated as a failure, fail-closed)."""
    fails: list[str] = []
    if not (MIN_PRICE <= price <= MAX_PRICE):
        fails.append(f"price {price} outside [{MIN_PRICE}, {MAX_PRICE}]")
    if market_cap < MIN_MARKET_CAP:
        fails.append(f"market cap {market_cap:.0f} < {MIN_MARKET_CAP:.0f}")
    if avg_volume < MIN_AVG_VOLUME:
        fails.append(f"avg volume {avg_volume:.0f} < {MIN_AVG_VOLUME:.0f}")
    if beta >= MAX_BETA:
        fails.append(f"beta {beta} >= {MAX_BETA}")
    if days_to_earnings is None:
        fails.append("earnings date unknown (fail-closed)")
    elif 0 <= days_to_earnings <= EARNINGS_BLACKOUT_DAYS:
        fails.append(f"earnings in {days_to_earnings}d (<= {EARNINGS_BLACKOUT_DAYS})")
    return FilterResult(not fails, fails)


def check_trend(price: float, sma50: float, sma200: float, ema20: float) -> FilterResult:
    """Uptrend stack: price > 50SMA, 50SMA > 200SMA, 20EMA > 50SMA."""
    fails: list[str] = []
    if not price > sma50:
        fails.append("price not above 50SMA")
    if not sma50 > sma200:
        fails.append("50SMA not above 200SMA")
    if not ema20 > sma50:
        fails.append("20EMA not above 50SMA")
    return FilterResult(not fails, fails)


def check_setup(pullback_pct: float, rsi_value: float, rel_strength: float) -> FilterResult:
    """Pullback depth, neutral RSI, and positive relative strength vs index."""
    fails: list[str] = []
    if not (PULLBACK_MIN_PCT <= pullback_pct <= PULLBACK_MAX_PCT):
        fails.append(f"pullback {pullback_pct:.1%} outside 3-10%")
    if not (RSI_SETUP_LOW <= rsi_value <= RSI_SETUP_HIGH):
        fails.append(f"RSI {rsi_value:.1f} outside [{RSI_SETUP_LOW}, {RSI_SETUP_HIGH}]")
    if not rel_strength > 0:
        fails.append("does not outperform index")
    return FilterResult(not fails, fails)


def check_trigger(
    bullish_reversal: bool, volume_surge: bool, rsi_crossed_back_above_40: bool
) -> FilterResult:
    """Entry trigger: reversal candle + volume surge + RSI reclaiming 40."""
    fails: list[str] = []
    if not bullish_reversal:
        fails.append("no bullish reversal candle")
    if not volume_surge:
        fails.append("volume not >= 20% above 10d avg")
    if not rsi_crossed_back_above_40:
        fails.append("RSI did not cross back above 40")
    return FilterResult(not fails, fails)


def qualifies(*results: FilterResult) -> FilterResult:
    """Aggregate: passes only if every stage passes; collects all failures."""
    fails: list[str] = []
    for r in results:
        fails.extend(r.failures)
    return FilterResult(not fails, fails)
