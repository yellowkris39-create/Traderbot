"""Screener orchestration: universe -> data -> indicators -> rules -> sizing.

`evaluate_symbol` is pure (takes already-fetched bars/fundamentals) so it
unit-tests without network. `Screener` wraps a DataProvider and adds the
network fetch + per-market index for relative strength / regime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from brokebyte.analysis import indicators as ind
from brokebyte.analysis import indicators_ext as ix
from brokebyte.screener import rules
from brokebyte.screener.data import DataProvider, Fundamentals
from brokebyte.screener.sizing_gbp import GbpTradePlan, size_trade_gbp

# Relative-strength / pullback windows
RS_LOOKBACK = 63          # ~3 months of trading days
PULLBACK_LOOKBACK = 20    # window for the recent swing high
SWING_LOW_LOOKBACK = 10   # window for the pullback's swing low (stop basis)
RSI_RECLAIM_WINDOW = 5    # bars within which RSI must have dipped <=40 then reclaimed
STOP_BELOW_SWING_PCT = 0.02  # stop 2% below the swing low
MIN_BARS = 210            # need >200 for the 200-day SMA


def index_symbol_for(symbol: str) -> str:
    """FTSE proxy for LSE ('.L') tickers, S&P 500 proxy otherwise."""
    return "^FTSE" if symbol.upper().endswith(".L") else "SPY"


def pullback_pct(bars: pd.DataFrame, lookback: int = PULLBACK_LOOKBACK) -> float:
    """(recent high - last close) / recent high over `lookback` bars."""
    window = bars.iloc[-lookback:]
    recent_high = float(window["high"].max())
    last_close = float(bars["close"].iloc[-1])
    if recent_high <= 0:
        return 0.0
    return (recent_high - last_close) / recent_high


def swing_low(bars: pd.DataFrame, lookback: int = SWING_LOW_LOOKBACK) -> float:
    return float(bars["low"].iloc[-lookback:].min())


def rsi_reclaimed_40(bars: pd.DataFrame, period: int = 14,
                     window: int = RSI_RECLAIM_WINDOW) -> bool:
    """True if RSI is now above 40 but dipped to <=40 within the last `window`
    bars — i.e. it crossed back up through 40 recently."""
    series = ix.rsi_series(bars, period).dropna()
    if len(series) < window + 1:
        return False
    now = float(series.iloc[-1])
    recent = series.iloc[-(window + 1):-1]
    return now > rules.RSI_TRIGGER_FLOOR and bool((recent <= rules.RSI_TRIGGER_FLOOR).any())


@dataclass
class ScreenResult:
    symbol: str
    passed: bool
    price: float | None = None
    failures: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    plan: GbpTradePlan | None = None


def _days_to_earnings(f: Fundamentals, now: datetime) -> float | None:
    if f.next_earnings is None:
        return None
    return (f.next_earnings - now).total_seconds() / 86400.0


def evaluate_symbol(
    symbol: str,
    bars: pd.DataFrame,
    index_bars: pd.DataFrame,
    fundamentals: Fundamentals,
    *,
    account: float = 500.0,
    now: datetime | None = None,
    fx_per_gbp: float = 1.0,
    skip_universe: bool = False,
) -> ScreenResult:
    """Run the full ruleset on one symbol. Pure — no I/O."""
    now = now or datetime.now(timezone.utc)

    if bars is None or len(bars) < MIN_BARS:
        return ScreenResult(symbol, False, failures=[f"insufficient history (<{MIN_BARS} bars)"])

    try:
        price = float(bars["close"].iloc[-1])
        sma50 = ind.sma(bars, 50)
        sma200 = ind.sma(bars, 200)
        ema20 = ix.ema(bars, 20)
        rsi_value = ix.rsi(bars, 14)
        avg_vol = ix.average_volume(bars, 50)
        rel = ix.relative_strength(bars, index_bars, RS_LOOKBACK)
        pb = pullback_pct(bars)
        reversal = ix.bullish_reversal(bars)
        vol_surge = ix.volume_surge(bars, 10, 1.20)
        rsi_cross = rsi_reclaimed_40(bars)
    except Exception as exc:  # noqa: BLE001
        return ScreenResult(symbol, False, failures=[f"indicator error: {exc}"])

    dte = _days_to_earnings(fundamentals, now)
    trend = rules.check_trend(price, sma50, sma200, ema20)
    setup = rules.check_setup(pb, rsi_value, rel)
    trigger = rules.check_trigger(reversal, vol_surge, rsi_cross)
    if skip_universe:
        agg = rules.qualifies(trend, setup, trigger)
    else:
        universe = rules.check_universe(
            price=price,
            market_cap=fundamentals.market_cap or 0.0,
            avg_volume=avg_vol,
            beta=fundamentals.beta if fundamentals.beta is not None else 999.0,
            days_to_earnings=dte,
        )
        agg = rules.qualifies(universe, trend, setup, trigger)

    if not agg.passed:
        return ScreenResult(symbol, False, price=price, failures=agg.failures)

    entry = price
    stop = round(swing_low(bars) * (1 - STOP_BELOW_SWING_PCT), 4)
    plan = size_trade_gbp(entry, stop, account=account, fx_per_gbp=fx_per_gbp)
    if plan is None:
        return ScreenResult(symbol, False, price=price,
                            failures=["no valid position size (stop too close/far)"])

    reasons = [
        f"uptrend: price>{sma50:.2f} (50SMA), 50SMA>{sma200:.2f} (200SMA), 20EMA>{sma50:.2f}",
        f"pullback {pb:.1%} into the 20EMA; RSI {rsi_value:.0f} (neutral)",
        f"outperforming index by {rel:.1%} over ~3mo",
        "trigger: bullish reversal candle + volume surge + RSI reclaimed 40",
    ]
    risks = [
        f"earnings in ~{dte:.0f} days" if dte is not None else "earnings date unknown",
        f"beta {fundamentals.beta}" if fundamentals.beta is not None else "beta unknown",
        "stop is a hard line; gaps through it can exceed the £5 risk",
        "max 3 open positions; skip if already at 3",
    ]
    if fundamentals.currency != "GBP" and fx_per_gbp == 1.0:
        risks.insert(0, f"⚠ {fundamentals.currency} priced: position size assumes "
                        "£1=1 unit — apply the live FX rate before trading")
    return ScreenResult(symbol, True, price=price, reasons=reasons, risks=risks, plan=plan)


class Screener:
    """Wraps a DataProvider; fetches bars + per-market index and evaluates."""

    def __init__(self, provider: DataProvider, *, account: float = 500.0) -> None:
        self._provider = provider
        self._account = account
        self._index_cache: dict[str, pd.DataFrame] = {}
        self._fx_cache: dict[str, float | None] = {}

    def _index_bars(self, symbol: str) -> pd.DataFrame:
        idx = index_symbol_for(symbol)
        if idx not in self._index_cache:
            self._index_cache[idx] = self._provider.daily_bars(idx)
        return self._index_cache[idx]

    def index_regime_ok(self, symbol: str) -> bool:
        """False if the relevant index closed below its own 200-day SMA
        (strategy says: go to cash, no new entries)."""
        bars = self._index_bars(symbol)
        if len(bars) < 200:
            return False
        return float(bars["close"].iloc[-1]) > ind.sma(bars, 200)

    def _fx_per_gbp(self, currency: str) -> float:
        """Units of instrument currency per £1; 1.0 for GBP or when the rate is
        unavailable (evaluate_symbol then flags the size as FX-pending)."""
        if currency == "GBP":
            return 1.0
        if currency not in self._fx_cache:
            fn = getattr(self._provider, "fx_per_gbp", None)
            self._fx_cache[currency] = fn(currency) if fn else None
        rate = self._fx_cache[currency]
        return rate if rate else 1.0

    def scan(self, symbols: list[str], *, now: datetime | None = None) -> list[ScreenResult]:
        """Evaluate every symbol; return only the ones that qualify."""
        results: list[ScreenResult] = []
        for sym in symbols:
            if not self.index_regime_ok(sym):
                continue  # index below 200SMA -> no new entries for that market
            try:
                bars = self._provider.daily_bars(sym)
                funds = self._provider.fundamentals(sym)
                index_bars = self._index_bars(sym)
            except Exception:
                continue
            fx = self._fx_per_gbp(funds.currency)
            res = evaluate_symbol(sym, bars, index_bars, funds,
                                  account=self._account, now=now, fx_per_gbp=fx)
            if res.passed:
                results.append(res)
        return results
