from brokebyte.risk.kill_switch import execute_kill_switch


class FakeTradingClient:
    def __init__(self, positions_closed, orders_cancelled):
        self._positions_closed = positions_closed
        self._orders_cancelled = orders_cancelled
        self.close_all_positions_called_with = None
        self.cancel_orders_called = False

    def close_all_positions(self, cancel_orders=None):
        self.close_all_positions_called_with = cancel_orders
        return self._positions_closed

    def cancel_orders(self):
        self.cancel_orders_called = True
        return self._orders_cancelled


def test_execute_kill_switch_flattens_and_cancels():
    client = FakeTradingClient(positions_closed=["AAPL", "MSFT"], orders_cancelled=["order1"])

    result = execute_kill_switch(client, reason="max daily loss breached")

    assert result.reason == "max daily loss breached"
    assert result.positions_closed == 2
    assert result.orders_cancelled == 1
    assert client.close_all_positions_called_with is True
    assert client.cancel_orders_called is True


def test_execute_kill_switch_with_nothing_open():
    client = FakeTradingClient(positions_closed=[], orders_cancelled=[])

    result = execute_kill_switch(client, reason="manual stop")

    assert result.positions_closed == 0
    assert result.orders_cancelled == 0
