import pytest

from brokebyte.backtest.costs import CostModel

MODEL = CostModel()


def test_buy_slippage_increases_price():
    assert MODEL.apply_slippage(100.0, "buy") == pytest.approx(100.05)


def test_sell_slippage_decreases_price():
    assert MODEL.apply_slippage(100.0, "sell") == pytest.approx(99.95)


def test_apply_slippage_rejects_unknown_side():
    with pytest.raises(ValueError):
        MODEL.apply_slippage(100.0, "hold")


def test_buy_side_has_no_regulatory_fees():
    assert MODEL.fees("buy", notional=10_000.0, qty=100) == 0.0


def test_sell_side_fees_below_taf_cap():
    fees = MODEL.fees("sell", notional=10_000.0, qty=100)

    # sec_fee = 10_000 * 0.0000278 = 0.278; taf = 100 * 0.000166 = 0.0166
    assert fees == pytest.approx(0.278 + 0.0166)


def test_sell_side_taf_is_capped():
    fees = MODEL.fees("sell", notional=1_000_000.0, qty=100_000)

    # sec_fee = 1_000_000 * 0.0000278 = 27.8; taf capped at 8.30
    assert fees == pytest.approx(27.8 + 8.30)
