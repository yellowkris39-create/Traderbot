"""Orchestration tests for the active exit manager using a fake broker."""

from __future__ import annotations

from datetime import datetime, timezone

from brokebyte.common import FilledOrder
from brokebyte.monitor.exit_manager import manage_open_positions


class _FakeStore:
    def __init__(self, rows):
        self._rows = rows
        self.outcomes = []

    def open_enter_decisions(self):
        return self._rows

    def record_outcome(self, decision_id, exit_price, exit_reason, pnl, closed_at=None):
        self.outcomes.append((decision_id, exit_price, exit_reason, pnl))


class _FakeBroker:
    def __init__(self, price, stop, fill=None):
        self._price, self._stop, self._fill = price, stop, fill
        self.replaced = []
        self.flattened = []

    def get_current_price(self, symbol):
        return self._price

    def get_open_stop(self, order_id):
        return ("leg-1", self._stop)

    def replace_stop(self, stop_leg_id, new_stop_price):
        self.replaced.append((stop_leg_id, new_stop_price))

    def flatten(self, symbol, order_id):
        self.flattened.append(symbol)
        return self._fill


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


def _row(**kw):
    base = dict(id=1, broker_order_id="o1", verdict_symbol="AAPL", plan_side="buy",
                plan_entry_price=100.0, plan_stop_price=98.0, plan_qty=10,
                recorded_at=datetime(2026, 6, 10, tzinfo=timezone.utc).isoformat())
    base.update(kw)
    return base


def test_moves_stop_to_breakeven_at_1r():
    store = _FakeStore([_row()])
    broker = _FakeBroker(price=102.0, stop=98.0)
    now = datetime(2026, 6, 12, tzinfo=timezone.utc)
    actions = manage_open_positions(broker, store, _Log(), now=now)
    assert broker.replaced == [("leg-1", 100.0)]
    assert actions and actions[0].kind == "move_breakeven"


def test_time_stop_closes_and_books_outcome():
    store = _FakeStore([_row()])
    fill = FilledOrder(fill_price=103.0, filled_at=datetime(2026, 6, 24, tzinfo=timezone.utc))
    broker = _FakeBroker(price=103.0, stop=100.0, fill=fill)
    now = datetime(2026, 6, 24, tzinfo=timezone.utc)
    actions = manage_open_positions(broker, store, _Log(), now=now)
    assert broker.flattened == ["AAPL"]
    assert store.outcomes == [(1, 103.0, "time_stop", 30.0)]
    assert actions[0].kind == "close_time_stop"


def test_no_action_when_below_1r_and_within_time():
    store = _FakeStore([_row()])
    broker = _FakeBroker(price=100.5, stop=98.0)
    now = datetime(2026, 6, 12, tzinfo=timezone.utc)
    actions = manage_open_positions(broker, store, _Log(), now=now)
    assert not broker.replaced and not broker.flattened and not actions


def test_skips_when_position_already_gone():
    store = _FakeStore([_row()])
    broker = _FakeBroker(price=None, stop=98.0)
    actions = manage_open_positions(broker, store, _Log(),
                                    now=datetime(2026, 6, 12, tzinfo=timezone.utc))
    assert not actions
