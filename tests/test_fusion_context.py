import pandas as pd

from brokebyte.fusion.context import TradeProposal, check_confluence
from brokebyte.guards.regime import Trend
from brokebyte.llm.provider import Direction, LLMVerdict, TimeHorizon


def make_verdict(**overrides):
    defaults = dict(
        material=True,
        symbol="AAPL",
        direction=Direction.LONG,
        confidence=0.8,
        time_horizon=TimeHorizon.SWING,
        reasoning="test",
        is_already_priced_in=False,
    )
    defaults.update(overrides)
    return LLMVerdict(**defaults)


def bars_from_closes(closes, high_offset=1.0, low_offset=1.0):
    return pd.DataFrame(
        {
            "high": [c + high_offset for c in closes],
            "low": [c - low_offset for c in closes],
            "close": closes,
        }
    )


def uptrend_bars(n=50):
    """closes 51..100, high=close+1, low=close-1 -> Trend.UP, ATR=2,
    last close=100, 20-bar support=80, resistance=101."""
    return bars_from_closes([51.0 + i for i in range(n)])


def downtrend_bars(n=50):
    """closes 149..100, high=close+1, low=close-1 -> Trend.DOWN, ATR=2,
    last close=100, 20-bar support=99, resistance=120."""
    return bars_from_closes([149.0 - i for i in range(n)])


def choppy_bars(n=20):
    return bars_from_closes([100.0] * n)


# --- trend agreement ---------------------------------------------------------


def test_confluence_passes_long_in_uptrend_away_from_resistance():
    result, proposal = check_confluence(make_verdict(direction=Direction.LONG), uptrend_bars(), price=100.0)

    assert result.ok
    assert proposal.regime.trend == Trend.UP
    assert proposal.support == 80.0
    assert proposal.resistance == 101.0


def test_confluence_passes_short_in_downtrend_away_from_support():
    result, proposal = check_confluence(make_verdict(direction=Direction.SHORT), downtrend_bars(), price=100.0)

    assert result.ok
    assert proposal.regime.trend == Trend.DOWN
    assert proposal.support == 99.0
    assert proposal.resistance == 120.0


def test_no_confluence_long_against_downtrend():
    result, proposal = check_confluence(make_verdict(direction=Direction.LONG), downtrend_bars(), price=100.0)

    assert not result.ok
    assert "verdict=long but trend=down" in result.reason
    assert proposal.regime.trend == Trend.DOWN


def test_no_confluence_short_against_uptrend():
    result, _ = check_confluence(make_verdict(direction=Direction.SHORT), uptrend_bars(), price=100.0)

    assert not result.ok
    assert "verdict=short but trend=up" in result.reason


def test_no_confluence_in_choppy_regime_for_either_direction():
    bars = choppy_bars()

    long_result, _ = check_confluence(make_verdict(direction=Direction.LONG), bars, price=100.0)
    short_result, _ = check_confluence(make_verdict(direction=Direction.SHORT), bars, price=100.0)

    assert not long_result.ok
    assert "trend=choppy" in long_result.reason
    assert not short_result.ok
    assert "trend=choppy" in short_result.reason


# --- support/resistance proximity --------------------------------------------


def test_no_confluence_long_blocked_near_resistance():
    # resistance == 101; price is 0.4% below it.
    result, proposal = check_confluence(make_verdict(direction=Direction.LONG), uptrend_bars(), price=100.6)

    assert not result.ok
    assert "resistance" in result.reason
    assert proposal.regime.trend == Trend.UP  # trend agrees; the level is what blocks


def test_no_confluence_short_blocked_near_support():
    # support == 99; price is 0.4% above it.
    result, proposal = check_confluence(make_verdict(direction=Direction.SHORT), downtrend_bars(), price=99.4)

    assert not result.ok
    assert "support" in result.reason


def test_confluence_passes_long_through_broken_resistance():
    # Price already above the recent high -> breakout, not "into a ceiling".
    result, _ = check_confluence(make_verdict(direction=Direction.LONG), uptrend_bars(), price=102.0)

    assert result.ok


def test_confluence_passes_short_through_broken_support():
    # Price already below the recent low -> breakdown, not "onto a floor".
    result, _ = check_confluence(make_verdict(direction=Direction.SHORT), downtrend_bars(), price=98.0)

    assert result.ok


# --- TradeProposal -------------------------------------------------------------


def test_proposal_returned_even_on_failure():
    verdict = make_verdict(direction=Direction.LONG)

    result, proposal = check_confluence(verdict, downtrend_bars(), price=100.0)

    assert not result.ok
    assert isinstance(proposal, TradeProposal)
    assert proposal.verdict == verdict


def test_insufficient_bars_fails_safe_via_choppy_trend():
    bars = bars_from_closes([100.0] * 5)  # below both the S/R lookback (20) and slow_period (50)
    verdict = make_verdict(direction=Direction.LONG)

    result, proposal = check_confluence(verdict, bars, price=100.0)

    assert not result.ok
    assert proposal.regime.trend == Trend.CHOPPY
    assert proposal.support == 100.0
    assert proposal.resistance == 100.0
