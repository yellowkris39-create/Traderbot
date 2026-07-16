"""Phase 1 tests: extended indicators, pure exit logic, screener rules, GBP sizing."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from brokebyte.analysis import indicators_ext as ix
from brokebyte.monitor import exits
from brokebyte.screener import rules
from brokebyte.screener.sizing_gbp import size_trade_gbp


def _bars(close, high=None, low=None, open_=None, volume=None):
    n = len(close)
    return pd.DataFrame({
        "open": open_ if open_ is not None else close,
        "high": high if high is not None else [c + 1 for c in close],
        "low": low if low is not None else [c - 1 for c in close],
        "close": close,
        "volume": volume if volume is not None else [1_000_000] * n,
    })


# --------------------------- indicators_ext ---------------------------

def test_ema_matches_pandas():
    b = _bars(list(range(1, 31)))
    expected = b["close"].ewm(span=10, adjust=False).mean().iloc[-1]
    assert ix.ema(b, 10) == pytest.approx(float(expected))


def test_rsi_strong_uptrend_is_high():
    b = _bars([float(x) for x in range(1, 40)])  # strictly rising
    assert ix.rsi(b, 14) > 90


def test_rsi_strong_downtrend_is_low():
    b = _bars([float(x) for x in range(40, 1, -1)])
    assert ix.rsi(b, 14) < 10


def test_rsi_raises_when_too_short():
    with pytest.raises(ValueError):
        ix.rsi(_bars([1.0, 2.0, 3.0]), 14)


def test_average_volume_and_surge():
    vol = [1_000_000] * 10 + [1_500_000]
    b = _bars([10.0] * 11, volume=vol)
    assert ix.average_volume(b, 10) == pytest.approx(np.mean(vol[-10:]))
    assert ix.volume_surge(b, 10, 1.20) is True  # 1.5M vs 1.0M trailing avg
    b2 = _bars([10.0] * 11, volume=[1_000_000] * 11)
    assert ix.volume_surge(b2, 10, 1.20) is False


def test_relative_strength_and_outperforms():
    stock = _bars([100.0] * 50 + [110.0] * 14)   # +10% recently
    index = _bars([100.0] * 64)                   # flat
    assert ix.relative_strength(stock, index, 63) > 0
    assert ix.outperforms(stock, index, 63) is True


def test_hammer_detected():
    # last bar: open 10.2 close 10.4 (small body up), low 8.0 (long lower wick), high 10.5
    b = _bars(close=[10.0, 10.4], open_=[10.0, 10.2], high=[10.5, 10.5], low=[9.5, 8.0])
    assert ix.is_hammer(b) is True


def test_bullish_engulfing_detected():
    # prev down (open 11 close 10), curr up engulfing (open 9.9 close 11.2)
    b = _bars(close=[10.0, 11.2], open_=[11.0, 9.9], high=[11.1, 11.3], low=[9.8, 9.8])
    assert ix.is_bullish_engulfing(b) is True


def test_morning_star_detected():
    b = _bars(
        close=[10.0, 8.5, 11.5],
        open_=[12.0, 8.6, 8.6],
        high=[12.1, 8.9, 11.6],
        low=[9.9, 8.3, 8.5],
    )
    assert ix.is_morning_star(b) is True


# --------------------------- exits (pure) ---------------------------

def test_trading_days_held_two_weeks():
    # Mon 2026-06-01 -> Mon 2026-06-15 == 10 weekdays
    assert exits.trading_days_held(datetime(2026, 6, 1), datetime(2026, 6, 15)) == 10


def test_time_stop_fires_after_max_days():
    a = exits.decide_exit(
        side="buy", entry_price=100, stop_price=98, current_stop_price=98,
        current_price=101, opened_at=datetime(2026, 6, 1), now=datetime(2026, 6, 15),
        max_holding_days=10,
    )
    assert a.kind == exits.CLOSE_TIME_STOP


def test_breakeven_moves_at_1r_long():
    a = exits.decide_exit(
        side="buy", entry_price=100, stop_price=98, current_stop_price=98,
        current_price=102, opened_at=datetime(2026, 6, 10), now=datetime(2026, 6, 12),
    )
    assert a.kind == exits.MOVE_BREAKEVEN and a.new_stop_price == 100


def test_breakeven_idempotent_when_stop_already_at_entry():
    # price between +1R (102) and +1.5R (103): break-even target == entry == live stop
    a = exits.decide_exit(
        side="buy", entry_price=100, stop_price=98, current_stop_price=100,
        current_price=102.5, opened_at=datetime(2026, 6, 10), now=datetime(2026, 6, 12),
    )
    assert a.kind == exits.NONE


def test_breakeven_long_not_yet_1r():
    a = exits.decide_exit(
        side="buy", entry_price=100, stop_price=98, current_stop_price=98,
        current_price=101, opened_at=datetime(2026, 6, 10), now=datetime(2026, 6, 12),
    )
    assert a.kind == exits.NONE


def test_breakeven_short_side():
    a = exits.decide_exit(
        side="sell", entry_price=100, stop_price=102, current_stop_price=102,
        current_price=98, opened_at=datetime(2026, 6, 10), now=datetime(2026, 6, 12),
    )
    assert a.kind == exits.MOVE_BREAKEVEN and a.new_stop_price == 100


# --------------------------- rules ---------------------------

def test_universe_pass():
    r = rules.check_universe(price=50, market_cap=2e9, avg_volume=2e6, beta=1.1, days_to_earnings=30)
    assert r.passed


def test_universe_fails_collect_all():
    r = rules.check_universe(price=2, market_cap=1e8, avg_volume=100, beta=2.0, days_to_earnings=3)
    assert not r.passed and len(r.failures) == 5


def test_universe_unknown_earnings_fails_closed():
    r = rules.check_universe(price=50, market_cap=2e9, avg_volume=2e6, beta=1.1, days_to_earnings=None)
    assert not r.passed


def test_trend_and_setup_and_trigger():
    assert rules.check_trend(price=105, sma50=100, sma200=90, ema20=102).passed
    assert not rules.check_trend(price=95, sma50=100, sma200=90, ema20=102).passed
    assert rules.check_setup(pullback_pct=0.05, rsi_value=50, rel_strength=0.04).passed
    assert not rules.check_setup(pullback_pct=0.20, rsi_value=70, rel_strength=-0.01).passed
    assert rules.check_trigger(True, True, True).passed
    assert not rules.check_trigger(True, False, True).passed


def test_qualifies_aggregates():
    good = rules.check_trend(price=105, sma50=100, sma200=90, ema20=102)
    bad = rules.check_trigger(False, False, False)
    assert rules.qualifies(good, bad).passed is False
    assert rules.qualifies(good).passed is True


# --------------------------- sizing_gbp ---------------------------

def test_sizing_worked_example_uncapped():
    # Strategy's worked example, with exposure cap effectively disabled.
    p = size_trade_gbp(50.0, 48.5, account=500, risk_pct=0.01, max_position_pct=10.0)
    assert p is not None
    assert p.shares == 3.33
    assert p.take_profit_price == pytest.approx(53.0)
    assert p.risk_amount == pytest.approx(5.0, abs=0.01)  # 3.33 * 1.5 = 4.995


def test_sizing_default_exposure_cap_binds():
    p = size_trade_gbp(50.0, 48.5)  # default 20% cap -> £100 / £50 = 2.0
    assert p is not None and p.shares == 2.0 and p.exposure_capped is True


def test_sizing_rejects_bad_stop():
    assert size_trade_gbp(50.0, 51.0) is None
    assert size_trade_gbp(-1.0, -2.0) is None


def test_trailing_stop_raises_above_breakeven_long():
    # entry 100, stop 98 (risk 2). price 110 -> +5R, well past 1.5R.
    # trail target = max(100, 110*0.98=107.8) = 107.8, above live stop 100.
    a = exits.decide_exit(
        side="buy", entry_price=100, stop_price=98, current_stop_price=100,
        current_price=110, opened_at=datetime(2026, 6, 10), now=datetime(2026, 6, 12),
    )
    assert a.kind == exits.MOVE_BREAKEVEN and a.new_stop_price == 107.8
    assert "trailing" in a.reason


def test_trailing_stop_ratchets_only_up_long():
    # live stop already at 108; price 110 -> trail 107.8 is NOT better -> no move.
    a = exits.decide_exit(
        side="buy", entry_price=100, stop_price=98, current_stop_price=108,
        current_price=110, opened_at=datetime(2026, 6, 10), now=datetime(2026, 6, 12),
    )
    assert a.kind == exits.NONE


def test_trailing_stop_never_below_entry_long():
    # price exactly at 1.5R (103): trail 103*0.98=100.94 > entry 100 -> uses 100.94
    a = exits.decide_exit(
        side="buy", entry_price=100, stop_price=98, current_stop_price=100,
        current_price=103, opened_at=datetime(2026, 6, 10), now=datetime(2026, 6, 12),
    )
    assert a.kind == exits.MOVE_BREAKEVEN and a.new_stop_price >= 100


def test_trailing_stop_short_side():
    # short: entry 100 stop 102 (risk 2). price 90 -> past 1.5R down.
    # trail target = min(100, 90*1.02=91.8) = 91.8, below live stop 100.
    a = exits.decide_exit(
        side="sell", entry_price=100, stop_price=102, current_stop_price=100,
        current_price=90, opened_at=datetime(2026, 6, 10), now=datetime(2026, 6, 12),
    )
    assert a.kind == exits.MOVE_BREAKEVEN and a.new_stop_price == 91.8


def test_universe_price_cap_raised_to_1000():
    """Kris 2026-07-16: $200 cap (blocking 42% of the universe) raised to $1000."""
    r = rules.check_universe(price=450, market_cap=2e12, avg_volume=5e6, beta=1.2, days_to_earnings=30)
    assert r.passed
    r = rules.check_universe(price=1500, market_cap=2e12, avg_volume=5e6, beta=1.2, days_to_earnings=30)
    assert not r.passed and any("price" in f for f in r.failures)
