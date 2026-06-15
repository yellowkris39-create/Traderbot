import pytest

from brokebyte.common import Quote
from brokebyte.guards.liquidity import check_fill_deviation, check_price_floor, check_spread
from brokebyte.risk.limits import RiskLimits

LIMITS = RiskLimits()  # min_price=5.0, max_spread_pct=0.005, fill_deviation_tolerance_pct=0.01


# --- price floor -----------------------------------------------------------


def test_price_above_floor_passes():
    assert check_price_floor(10.0, LIMITS).ok


def test_price_at_floor_passes():
    assert check_price_floor(LIMITS.min_price, LIMITS).ok


def test_price_below_floor_fails():
    result = check_price_floor(4.99, LIMITS)

    assert not result.ok
    assert "below minimum" in result.reason


# --- spread ------------------------------------------------------------


def test_tight_spread_passes():
    quote = Quote(bid_price=99.95, ask_price=100.05)  # 0.1% spread

    assert check_spread(quote, LIMITS).ok


def test_wide_spread_fails():
    quote = Quote(bid_price=99.0, ask_price=101.0)  # ~2% spread

    result = check_spread(quote, LIMITS)

    assert not result.ok
    assert "spread" in result.reason


# --- fill deviation ------------------------------------------------------


def test_fill_within_tolerance_passes():
    assert check_fill_deviation(expected_price=100.0, fill_price=100.5, limits=LIMITS).ok


def test_fill_beyond_tolerance_fails():
    result = check_fill_deviation(expected_price=100.0, fill_price=102.0, limits=LIMITS)

    assert not result.ok
    assert "deviates" in result.reason


@pytest.mark.parametrize("expected_price", [0.0, -1.0])
def test_fill_deviation_skipped_when_expected_price_not_positive(expected_price):
    assert check_fill_deviation(expected_price=expected_price, fill_price=100.0, limits=LIMITS).ok
