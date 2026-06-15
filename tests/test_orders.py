from alpaca.trading.enums import OrderClass, OrderSide, OrderType, TimeInForce

from brokebyte.risk.orders import build_bracket_order
from brokebyte.risk.sizing import PositionPlan


def test_build_bracket_order_for_buy():
    plan = PositionPlan(
        symbol="AAPL",
        side="buy",
        qty=10,
        entry_price=100.0,
        stop_price=96.0,
        take_profit_price=108.0,
        risk_amount=40.0,
        notional=1000.0,
    )

    order = build_bracket_order(plan)

    assert order.symbol == "AAPL"
    assert order.qty == 10
    assert order.side == OrderSide.BUY
    assert order.type == OrderType.MARKET
    assert order.time_in_force == TimeInForce.DAY
    assert order.order_class == OrderClass.BRACKET
    assert order.stop_loss.stop_price == 96.0
    assert order.take_profit.limit_price == 108.0


def test_build_bracket_order_for_sell():
    plan = PositionPlan(
        symbol="AAPL",
        side="sell",
        qty=10,
        entry_price=100.0,
        stop_price=104.0,
        take_profit_price=92.0,
        risk_amount=40.0,
        notional=1000.0,
    )

    order = build_bracket_order(plan)

    assert order.side == OrderSide.SELL
    assert order.stop_loss.stop_price == 104.0
    assert order.take_profit.limit_price == 92.0
