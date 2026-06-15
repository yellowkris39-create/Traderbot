import pytest

from brokebyte.risk.limits import RiskLimits
from brokebyte.risk.sizing import size_position

LIMITS = RiskLimits()  # 0.5% risk/trade, 10% max exposure, 2x ATR stop, 4x ATR TP


def test_buy_sizes_by_exposure_cap_when_it_binds():
    # equity=100k, entry=100, atr=2 -> stop_distance=4
    # qty_by_risk = floor(500/4) = 125; qty_by_exposure = floor(10000/100) = 100
    plan = size_position("AAPL", "buy", entry_price=100.0, atr=2.0, equity=100_000, limits=LIMITS)

    assert plan is not None
    assert plan.qty == 100
    assert plan.stop_price == pytest.approx(96.0)
    assert plan.take_profit_price == pytest.approx(108.0)
    assert plan.notional == pytest.approx(10_000.0)
    assert plan.risk_amount == pytest.approx(400.0)


def test_sell_stop_and_take_profit_are_mirrored():
    plan = size_position("AAPL", "sell", entry_price=100.0, atr=2.0, equity=100_000, limits=LIMITS)

    assert plan is not None
    assert plan.stop_price == pytest.approx(104.0)
    assert plan.take_profit_price == pytest.approx(92.0)


def test_risk_per_trade_cap_binds_for_volatile_stock():
    # entry=50, atr=8 -> stop_distance=16
    # qty_by_risk = floor(500/16) = 31; qty_by_exposure = floor(10000/50) = 200
    plan = size_position("XYZ", "buy", entry_price=50.0, atr=8.0, equity=100_000, limits=LIMITS)

    assert plan is not None
    assert plan.qty == 31


def test_size_multiplier_shrinks_position():
    full = size_position("AAPL", "buy", entry_price=100.0, atr=2.0, equity=100_000, limits=LIMITS)
    half = size_position(
        "AAPL", "buy", entry_price=100.0, atr=2.0, equity=100_000, limits=LIMITS, size_multiplier=0.5
    )

    assert full is not None and half is not None
    assert half.qty < full.qty


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(entry_price=0.0, atr=2.0, equity=100_000),
        dict(entry_price=100.0, atr=0.0, equity=100_000),
        dict(entry_price=100.0, atr=2.0, equity=0.0),
        dict(entry_price=100.0, atr=2.0, equity=100_000, size_multiplier=0.0),
    ],
)
def test_invalid_inputs_return_none(kwargs):
    assert size_position("AAPL", "buy", limits=LIMITS, **kwargs) is None


def test_qty_below_one_returns_none():
    # Tiny equity -> risk_dollars too small for even 1 share given the stop distance.
    plan = size_position("AAPL", "buy", entry_price=100.0, atr=2.0, equity=10.0, limits=LIMITS)

    assert plan is None


def test_invalid_side_raises():
    with pytest.raises(ValueError):
        size_position("AAPL", "hold", entry_price=100.0, atr=2.0, equity=100_000, limits=LIMITS)
