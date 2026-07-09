"""Tests for brokebyte.monitor.reconcile (position-monitoring loop)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from brokebyte.common import FilledOrder
from brokebyte.fusion.context import TradeProposal
from brokebyte.guards.regime import Regime, Trend
from brokebyte.ingestion.events import NewsEvent
from brokebyte.llm.provider import Direction, LLMVerdict, TimeHorizon
from brokebyte.memory.store import DecisionStore
from brokebyte.monitor.reconcile import (
    OutcomeRecord,
    _compute_pnl,
    _infer_exit_reason,
    reconcile_open_positions,
)
from brokebyte.risk.gate import GateDecision
from brokebyte.risk.sizing import PositionPlan


# ---------------------------------------------------------------------------
# Fake broker
# ---------------------------------------------------------------------------

class FakeBroker:
    """Minimal PositionBrokerLike implementation for testing."""

    def __init__(
        self,
        open_symbols: set[str],
        exit_orders: dict[str, FilledOrder | None],
        order_exit_fills: dict[str, FilledOrder | None] | None = None,
    ) -> None:
        self._open_symbols = open_symbols
        self._exit_orders = exit_orders
        self._order_exit_fills = order_exit_fills or {}

    def get_position_symbols(self) -> set[str]:
        return self._open_symbols

    def get_filled_exit_order(
        self, symbol: str, after: datetime, plan_side: str
    ) -> FilledOrder | None:
        return self._exit_orders.get(symbol)

    def get_order_exit_fill(self, order_id: str) -> FilledOrder | None:
        return self._order_exit_fills.get(order_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_event(**overrides):
    defaults = dict(
        id="evt-1", headline="H", summary="S", symbols=["AAPL"], source="test"
    )
    defaults.update(overrides)
    return NewsEvent(**defaults)


def make_verdict(**overrides):
    defaults = dict(
        material=True,
        symbol="AAPL",
        direction=Direction.LONG,
        confidence=0.8,
        time_horizon=TimeHorizon.SWING,
        reasoning="r",
        is_already_priced_in=False,
    )
    defaults.update(overrides)
    return LLMVerdict(**defaults)


def make_plan(**overrides):
    defaults = dict(
        symbol="AAPL",
        side="buy",
        qty=10,
        entry_price=100.0,
        stop_price=96.0,
        take_profit_price=108.0,
        risk_amount=40.0,
        notional=1000.0,
    )
    defaults.update(overrides)
    return PositionPlan(**defaults)


def make_proposal(trend: Trend = Trend.UP):
    verdict = make_verdict()
    return TradeProposal(
        verdict=verdict,
        regime=Regime(trend=trend, high_volatility=False, size_multiplier=1.0),
        support=80.0,
        resistance=120.0,
    )


def seed_open_enter(store: DecisionStore, broker_order_id: str | None = None, **overrides) -> int:
    """Insert an ENTER decision with no outcome. Returns decision_id."""
    plan_overrides = {k: v for k, v in overrides.items()
                      if k in ("symbol", "side", "qty", "entry_price",
                               "stop_price", "take_profit_price")}
    verdict_overrides = {k: v for k, v in overrides.items() if k == "symbol"}
    plan = make_plan(**plan_overrides)
    verdict = make_verdict(**verdict_overrides)
    proposal = make_proposal()
    decision = GateDecision(plan=plan, reason="enter", proposal=proposal)
    decision_id = store.record(make_event(), verdict, decision)
    if broker_order_id:
        store.update_order_id(decision_id, broker_order_id)
    return decision_id


# ---------------------------------------------------------------------------
# Unit tests for pure helper functions
# ---------------------------------------------------------------------------


def test_infer_exit_reason_take_profit():
    assert _infer_exit_reason(108.0, stop_price=96.0, take_profit_price=108.0) == "take_profit"


def test_infer_exit_reason_stop():
    assert _infer_exit_reason(96.0, stop_price=96.0, take_profit_price=108.0) == "stop"


def test_infer_exit_reason_favours_take_profit_when_equidistant():
    # fill exactly halfway → take_profit wins the tie
    assert _infer_exit_reason(102.0, stop_price=96.0, take_profit_price=108.0) == "take_profit"


def test_infer_exit_reason_short_stop():
    # Short: stop above entry, take_profit below entry
    assert _infer_exit_reason(104.0, stop_price=104.0, take_profit_price=92.0) == "stop"


def test_infer_exit_reason_short_take_profit():
    assert _infer_exit_reason(92.0, stop_price=104.0, take_profit_price=92.0) == "take_profit"


def test_compute_pnl_long_profit():
    assert _compute_pnl(100.0, 108.0, qty=10, plan_side="buy") == pytest.approx(80.0)


def test_compute_pnl_long_loss():
    assert _compute_pnl(100.0, 96.0, qty=10, plan_side="buy") == pytest.approx(-40.0)


def test_compute_pnl_short_profit():
    assert _compute_pnl(100.0, 92.0, qty=10, plan_side="sell") == pytest.approx(80.0)


def test_compute_pnl_short_loss():
    assert _compute_pnl(100.0, 104.0, qty=10, plan_side="sell") == pytest.approx(-40.0)


# ---------------------------------------------------------------------------
# Integration tests for reconcile_open_positions
# ---------------------------------------------------------------------------


def test_reconcile_empty_store_returns_no_outcomes(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    broker = FakeBroker(open_symbols=set(), exit_orders={})

    outcomes = reconcile_open_positions(broker, store)

    assert outcomes == []


def test_reconcile_skips_hold_decisions(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    event, verdict = make_event(), make_verdict()
    store.record(event, verdict, GateDecision(plan=None, reason="hold"))
    broker = FakeBroker(open_symbols=set(), exit_orders={})

    outcomes = reconcile_open_positions(broker, store)

    assert outcomes == []


def test_reconcile_skips_position_still_open(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    seed_open_enter(store)
    broker = FakeBroker(open_symbols={"AAPL"}, exit_orders={})  # still open

    outcomes = reconcile_open_positions(broker, store)

    assert outcomes == []
    assert store.open_enter_decisions() != []  # no outcome written


def test_reconcile_records_take_profit_outcome(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    seed_open_enter(store, entry_price=100.0, stop_price=96.0, take_profit_price=108.0)
    filled_at = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    broker = FakeBroker(
        open_symbols=set(),  # position closed
        exit_orders={"AAPL": FilledOrder(fill_price=108.0, filled_at=filled_at)},
    )

    outcomes = reconcile_open_positions(broker, store)

    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.symbol == "AAPL"
    assert o.exit_reason == "take_profit"
    assert o.exit_price == pytest.approx(108.0)
    assert o.pnl == pytest.approx(80.0)  # (108-100) * 10


def test_reconcile_records_stop_outcome(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    seed_open_enter(store, entry_price=100.0, stop_price=96.0, take_profit_price=108.0)
    filled_at = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    broker = FakeBroker(
        open_symbols=set(),
        exit_orders={"AAPL": FilledOrder(fill_price=96.0, filled_at=filled_at)},
    )

    outcomes = reconcile_open_positions(broker, store)

    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.exit_reason == "stop"
    assert o.pnl == pytest.approx(-40.0)  # (96-100) * 10


def test_reconcile_writes_outcome_to_store(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    seed_open_enter(store)
    filled_at = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    broker = FakeBroker(
        open_symbols=set(),
        exit_orders={"AAPL": FilledOrder(fill_price=108.0, filled_at=filled_at)},
    )

    reconcile_open_positions(broker, store)

    # Outcome is now persisted: no more open decisions, and pnl is recorded
    assert store.open_enter_decisions() == []
    assert store.closed_trade_pnls() == [pytest.approx(80.0)]


def test_reconcile_handles_missing_exit_order(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    seed_open_enter(store)
    broker = FakeBroker(open_symbols=set(), exit_orders={"AAPL": None})

    outcomes = reconcile_open_positions(broker, store)

    # Warning logged but no outcome recorded; decision still open
    assert outcomes == []
    assert store.open_enter_decisions() != []


def test_reconcile_handles_multiple_decisions_same_symbol_no_order_id(tmp_path):
    """Legacy fallback: decisions without order IDs all reconcile via symbol."""
    store = DecisionStore(tmp_path / "d.db")
    seed_open_enter(store, entry_price=100.0, stop_price=96.0, take_profit_price=108.0)
    seed_open_enter(store, entry_price=102.0, stop_price=98.0, take_profit_price=110.0)
    filled_at = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    broker = FakeBroker(
        open_symbols=set(),
        exit_orders={"AAPL": FilledOrder(fill_price=108.0, filled_at=filled_at)},
    )

    outcomes = reconcile_open_positions(broker, store)

    assert len(outcomes) == 2


def test_reconcile_only_processes_decisions_with_no_outcome(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    row_id = seed_open_enter(store)
    # Manually close the first decision
    store.record_outcome(row_id, exit_price=108.0, exit_reason="take_profit", pnl=80.0)
    # Add a second open one
    seed_open_enter(store, entry_price=100.0, stop_price=96.0, take_profit_price=108.0)

    filled_at = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    broker = FakeBroker(
        open_symbols=set(),
        exit_orders={"AAPL": FilledOrder(fill_price=108.0, filled_at=filled_at)},
    )

    outcomes = reconcile_open_positions(broker, store)

    # Only the second (open) decision is processed
    assert len(outcomes) == 1


def test_reconcile_short_position(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    event = make_event()
    verdict = make_verdict(direction=Direction.SHORT)
    plan = make_plan(
        side="sell",
        entry_price=100.0,
        stop_price=104.0,  # short: stop above entry
        take_profit_price=92.0,  # short: tp below entry
    )
    proposal = make_proposal(Trend.DOWN)
    decision = GateDecision(plan=plan, reason="short enter", proposal=proposal)
    store.record(event, verdict, decision)

    filled_at = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    broker = FakeBroker(
        open_symbols=set(),
        exit_orders={"AAPL": FilledOrder(fill_price=92.0, filled_at=filled_at)},
    )

    outcomes = reconcile_open_positions(broker, store)

    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.exit_reason == "take_profit"
    assert o.pnl == pytest.approx(80.0)  # (100-92) * 10


# ---------------------------------------------------------------------------
# Order-based reconciliation tests
# ---------------------------------------------------------------------------


def test_order_based_reconcile_uses_order_fill(tmp_path):
    """Decision with broker_order_id reconciles via order, not symbol presence."""
    store = DecisionStore(tmp_path / "d.db")
    seed_open_enter(store, broker_order_id="order-1",
                    entry_price=100.0, stop_price=96.0, take_profit_price=108.0)
    filled_at = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    broker = FakeBroker(
        open_symbols={"AAPL"},  # symbol still in positions, but order filled
        exit_orders={},
        order_exit_fills={"order-1": FilledOrder(fill_price=108.0, filled_at=filled_at)},
    )

    outcomes = reconcile_open_positions(broker, store)

    assert len(outcomes) == 1
    assert outcomes[0].pnl == pytest.approx(80.0)


def test_order_based_skips_unfilled_order(tmp_path):
    """Decision with broker_order_id whose exit leg hasn't filled is skipped."""
    store = DecisionStore(tmp_path / "d.db")
    seed_open_enter(store, broker_order_id="order-1")
    broker = FakeBroker(
        open_symbols={"AAPL"},
        exit_orders={},
        order_exit_fills={"order-1": None},
    )

    outcomes = reconcile_open_positions(broker, store)

    assert outcomes == []
    assert store.open_enter_decisions() != []


def test_order_based_multi_decision_same_symbol_independent(tmp_path):
    """Three EOSE decisions with different order IDs reconcile independently."""
    store = DecisionStore(tmp_path / "d.db")
    seed_open_enter(store, broker_order_id="order-A", symbol="EOSE",
                    entry_price=7.0, stop_price=6.0, take_profit_price=9.0, qty=100)
    seed_open_enter(store, broker_order_id="order-B", symbol="EOSE",
                    entry_price=7.5, stop_price=6.5, take_profit_price=9.5, qty=100)
    seed_open_enter(store, broker_order_id="order-C", symbol="EOSE",
                    entry_price=8.0, stop_price=7.0, take_profit_price=10.0, qty=100)

    filled_at = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    broker = FakeBroker(
        open_symbols={"EOSE"},
        exit_orders={},
        order_exit_fills={
            "order-A": FilledOrder(fill_price=9.0, filled_at=filled_at),
            "order-B": None,  # still open
            "order-C": FilledOrder(fill_price=7.0, filled_at=filled_at),
        },
    )

    outcomes = reconcile_open_positions(broker, store)

    assert len(outcomes) == 2
    by_id = {o.decision_id: o for o in outcomes}
    ids = sorted(by_id.keys())
    assert by_id[ids[0]].pnl == pytest.approx(200.0)   # (9-7)*100 = take profit
    assert by_id[ids[1]].pnl == pytest.approx(-100.0)   # (7-8)*100 = stop

    remaining = store.open_enter_decisions()
    assert len(remaining) == 1
    assert remaining[0]["broker_order_id"] == "order-B"


def test_mixed_order_and_symbol_based(tmp_path):
    """Decisions with order IDs use order path; those without use symbol fallback."""
    store = DecisionStore(tmp_path / "d.db")
    seed_open_enter(store, broker_order_id="order-1", symbol="AAPL",
                    entry_price=100.0, stop_price=96.0, take_profit_price=108.0)
    seed_open_enter(store, symbol="MSFT",
                    entry_price=200.0, stop_price=190.0, take_profit_price=220.0)

    filled_at = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    broker = FakeBroker(
        open_symbols=set(),  # both symbols gone
        exit_orders={"MSFT": FilledOrder(fill_price=220.0, filled_at=filled_at)},
        order_exit_fills={"order-1": FilledOrder(fill_price=108.0, filled_at=filled_at)},
    )

    outcomes = reconcile_open_positions(broker, store)

    assert len(outcomes) == 2
    symbols = {o.symbol for o in outcomes}
    assert symbols == {"AAPL", "MSFT"}


# ---------------------------------------------------------------------------
# Flatten/close_position fallback (2026-07-09 fix): a position closed by a
# NON-bracket order (exit manager flatten, manual close) must still get its
# outcome booked — observed live as NOK, then EOSE/HR/NBIS/SBRA phantoms.
# ---------------------------------------------------------------------------


def test_order_id_row_with_vanished_position_books_via_symbol_fallback(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    seed_open_enter(store, broker_order_id="ord-hr")
    fill = FilledOrder(fill_price=95.0, filled_at=datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc))
    broker = FakeBroker(
        open_symbols=set(),                       # position gone from Alpaca
        exit_orders={"AAPL": fill},               # flatten fill in order history
        order_exit_fills={"ord-hr": None},        # bracket has no filled exit leg
    )
    outcomes = reconcile_open_positions(broker, store)
    assert len(outcomes) == 1
    assert outcomes[0].exit_reason == "flattened_or_external"
    assert outcomes[0].pnl == pytest.approx((95.0 - 100.0) * 10)
    assert store.open_enter_decisions() == []


def test_order_id_row_position_still_open_is_left_alone(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    seed_open_enter(store, broker_order_id="ord-1")
    broker = FakeBroker(open_symbols={"AAPL"}, exit_orders={}, order_exit_fills={"ord-1": None})
    assert reconcile_open_positions(broker, store) == []
    assert len(store.open_enter_decisions()) == 1


def test_multi_row_symbol_is_flagged_not_multibooked(tmp_path):
    """Three open rows for one aggregated position (the live EOSE case) must
    NOT each book the same exit fill — they need manual cleanup."""
    store = DecisionStore(tmp_path / "d.db")
    for oid in ("ord-a", "ord-b", "ord-c"):
        seed_open_enter(store, broker_order_id=oid)
    fill = FilledOrder(fill_price=95.0, filled_at=datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc))
    broker = FakeBroker(open_symbols=set(), exit_orders={"AAPL": fill},
                        order_exit_fills={"ord-a": None, "ord-b": None, "ord-c": None})
    outcomes = reconcile_open_positions(broker, store)
    assert outcomes == []
    assert len(store.open_enter_decisions()) == 3  # untouched, flagged in logs


def test_fallback_no_fill_found_stays_open(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    seed_open_enter(store, broker_order_id="ord-x")
    broker = FakeBroker(open_symbols=set(), exit_orders={}, order_exit_fills={"ord-x": None})
    assert reconcile_open_positions(broker, store) == []
    assert len(store.open_enter_decisions()) == 1
