import sqlite3

import pytest

from brokebyte.fusion.context import TradeProposal
from brokebyte.guards.regime import Regime, Trend
from brokebyte.ingestion.events import NewsEvent
from brokebyte.llm.provider import Direction, LLMVerdict, TimeHorizon
from brokebyte.memory.store import DecisionStore
from brokebyte.risk.gate import GateDecision
from brokebyte.risk.sizing import PositionPlan

# Pre-Phase-5c schema, before exit_price/exit_reason/pnl/closed_at existed --
# used to verify DecisionStore._migrate adds them to old decisions.db files
# without losing existing rows.
OLD_SCHEMA = """
CREATE TABLE decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    event_id TEXT NOT NULL,
    headline TEXT NOT NULL,
    symbols TEXT NOT NULL,
    source TEXT NOT NULL,
    verdict_material INTEGER NOT NULL,
    verdict_symbol TEXT,
    verdict_direction TEXT NOT NULL,
    verdict_confidence REAL NOT NULL,
    verdict_time_horizon TEXT NOT NULL,
    verdict_reasoning TEXT NOT NULL,
    verdict_is_already_priced_in INTEGER NOT NULL,
    regime_trend TEXT,
    regime_high_volatility INTEGER,
    regime_size_multiplier REAL,
    support REAL,
    resistance REAL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    kill_switch_reason TEXT,
    plan_side TEXT,
    plan_qty INTEGER,
    plan_entry_price REAL,
    plan_stop_price REAL,
    plan_take_profit_price REAL,
    plan_risk_amount REAL,
    plan_notional REAL
)
"""


def make_event(**overrides):
    defaults = dict(
        id="evt-1",
        headline="Example Corp announces new product line",
        summary="Routine product announcement.",
        symbols=["AAPL"],
        source="test",
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
        reasoning="test reasoning",
        is_already_priced_in=False,
    )
    defaults.update(overrides)
    return LLMVerdict(**defaults)


def test_new_store_is_empty(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")

    assert store.count() == 0
    assert store.recent() == []


def test_record_hold_before_confluence(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    event = make_event()
    verdict = make_verdict(
        material=False, direction=Direction.NONE, confidence=0.0, time_horizon=TimeHorizon.NONE, reasoning="not material"
    )
    decision = GateDecision(plan=None, reason="verdict not material")

    row_id = store.record(event, verdict, decision)

    assert row_id == 1
    assert store.count() == 1
    row = store.recent(1)[0]
    assert row["event_id"] == "evt-1"
    assert row["symbols"] == "AAPL"
    assert row["action"] == "HOLD"
    assert row["reason"] == "verdict not material"
    assert row["regime_trend"] is None
    assert row["plan_qty"] is None


def test_record_hold_with_proposal_no_plan(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    event = make_event()
    verdict = make_verdict(direction=Direction.LONG)
    proposal = TradeProposal(
        verdict=verdict,
        regime=Regime(trend=Trend.DOWN, high_volatility=False, size_multiplier=1.0),
        support=99.0,
        resistance=120.0,
    )
    decision = GateDecision(
        plan=None,
        reason="module 3 (confluence): no confluence: verdict=long but trend=down",
        proposal=proposal,
    )

    store.record(event, verdict, decision)

    row = store.recent(1)[0]
    assert row["action"] == "HOLD"
    assert row["regime_trend"] == "down"
    assert row["regime_high_volatility"] == 0
    assert row["regime_size_multiplier"] == 1.0
    assert row["support"] == 99.0
    assert row["resistance"] == 120.0
    assert row["plan_qty"] is None


def test_record_enter_with_plan(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    event = make_event()
    verdict = make_verdict(direction=Direction.LONG)
    proposal = TradeProposal(
        verdict=verdict,
        regime=Regime(trend=Trend.UP, high_volatility=False, size_multiplier=1.0),
        support=80.0,
        resistance=101.0,
    )
    plan = PositionPlan(
        symbol="AAPL",
        side="buy",
        qty=100,
        entry_price=100.0,
        stop_price=96.0,
        take_profit_price=108.0,
        risk_amount=400.0,
        notional=10_000.0,
    )
    decision = GateDecision(plan=plan, reason="entry approved (trend=up, size_multiplier=1.0)", proposal=proposal)

    store.record(event, verdict, decision)

    row = store.recent(1)[0]
    assert row["action"] == "ENTER"
    assert row["regime_trend"] == "up"
    assert row["plan_side"] == "buy"
    assert row["plan_qty"] == 100
    assert row["plan_entry_price"] == 100.0
    assert row["plan_stop_price"] == 96.0
    assert row["plan_take_profit_price"] == 108.0
    assert row["plan_notional"] == 10_000.0


def test_record_persists_kill_switch_reason(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    event = make_event()
    verdict = make_verdict()
    decision = GateDecision(
        plan=None,
        reason="portfolio: daily loss 2.00% >= halt limit 2.00%",
        kill_switch_reason="daily loss 2.00% >= halt limit 2.00%",
    )

    store.record(event, verdict, decision)

    row = store.recent(1)[0]
    assert row["kill_switch_reason"] == "daily loss 2.00% >= halt limit 2.00%"


def test_recent_orders_most_recent_first(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    event = make_event()

    store.record(event, make_verdict(symbol="AAPL"), GateDecision(plan=None, reason="first"))
    store.record(event, make_verdict(symbol="MSFT"), GateDecision(plan=None, reason="second"))

    rows = store.recent(10)

    assert len(rows) == 2
    assert rows[0]["reason"] == "second"
    assert rows[1]["reason"] == "first"
    assert store.count() == 2


def make_plan(**overrides):
    defaults = dict(
        symbol="AAPL",
        side="buy",
        qty=100,
        entry_price=100.0,
        stop_price=96.0,
        take_profit_price=108.0,
        risk_amount=400.0,
        notional=10_000.0,
    )
    defaults.update(overrides)
    return PositionPlan(**defaults)


def test_record_outcome_updates_row(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    event = make_event()
    verdict = make_verdict(direction=Direction.LONG)
    decision = GateDecision(plan=make_plan(), reason="entry approved")
    row_id = store.record(event, verdict, decision)

    store.record_outcome(row_id, exit_price=108.0, exit_reason="take_profit", pnl=800.0)

    row = store.recent(1)[0]
    assert row["exit_price"] == 108.0
    assert row["exit_reason"] == "take_profit"
    assert row["pnl"] == 800.0
    assert row["closed_at"] is not None


def test_record_outcome_unknown_id_raises(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")

    with pytest.raises(ValueError):
        store.record_outcome(999, exit_price=100.0, exit_reason="stop", pnl=-50.0)


def test_closed_trade_pnls_returns_only_closed_in_order(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    event = make_event()
    verdict = make_verdict(direction=Direction.LONG)

    id1 = store.record(event, verdict, GateDecision(plan=make_plan(), reason="enter 1"))
    store.record(event, verdict, GateDecision(plan=None, reason="hold"))  # no outcome
    id3 = store.record(event, verdict, GateDecision(plan=make_plan(), reason="enter 2"))

    store.record_outcome(id1, exit_price=108.0, exit_reason="take_profit", pnl=80.0)
    store.record_outcome(id3, exit_price=96.0, exit_reason="stop", pnl=-40.0)

    assert store.closed_trade_pnls() == [80.0, -40.0]


def test_regime_coverage_tallies_trends(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    event = make_event()
    verdict = make_verdict(direction=Direction.LONG)

    up_proposal = TradeProposal(
        verdict=verdict, regime=Regime(trend=Trend.UP, high_volatility=False, size_multiplier=1.0), support=80.0, resistance=120.0
    )
    down_proposal = TradeProposal(
        verdict=verdict, regime=Regime(trend=Trend.DOWN, high_volatility=False, size_multiplier=1.0), support=80.0, resistance=120.0
    )

    store.record(event, verdict, GateDecision(plan=None, reason="r1", proposal=up_proposal))
    store.record(event, verdict, GateDecision(plan=None, reason="r2", proposal=down_proposal))
    store.record(event, verdict, GateDecision(plan=None, reason="r3", proposal=up_proposal))
    store.record(event, verdict, GateDecision(plan=None, reason="r4"))  # no proposal -> regime_trend NULL

    coverage = store.regime_coverage()

    assert coverage == {Trend.UP: 2, Trend.DOWN: 1, Trend.CHOPPY: 0}


def test_migration_adds_outcome_columns_to_old_schema(tmp_path):
    path = tmp_path / "decisions.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(OLD_SCHEMA)
        conn.execute(
            "INSERT INTO decisions ("
            "recorded_at, event_id, headline, symbols, source, verdict_material, "
            "verdict_direction, verdict_confidence, verdict_time_horizon, "
            "verdict_reasoning, verdict_is_already_priced_in, action, reason"
            ") VALUES ("
            "'2026-01-01T00:00:00+00:00', 'evt-old', 'old headline', 'AAPL', 'test', 0, "
            "'none', 0.0, 'none', 'pre-migration row', 0, 'HOLD', 'old reason')"
        )
        conn.commit()
    finally:
        conn.close()

    store = DecisionStore(path)

    assert store.count() == 1
    row = store.recent(1)[0]
    assert row["event_id"] == "evt-old"
    assert row["pnl"] is None

    store.record_outcome(row["id"], exit_price=100.0, exit_reason="stop", pnl=-10.0)
    assert store.closed_trade_pnls() == [-10.0]


def test_open_enter_decisions_returns_only_enters_with_no_outcome(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    event = make_event()
    verdict = make_verdict(direction=Direction.LONG)

    id1 = store.record(event, verdict, GateDecision(plan=make_plan(), reason="enter 1"))
    store.record(event, verdict, GateDecision(plan=None, reason="hold"))  # HOLD — excluded
    id3 = store.record(event, verdict, GateDecision(plan=make_plan(), reason="enter 2"))

    # Close the first enter
    store.record_outcome(id1, exit_price=108.0, exit_reason="take_profit", pnl=80.0)

    open_rows = store.open_enter_decisions()

    assert len(open_rows) == 1
    assert open_rows[0]["id"] == id3


def test_update_order_id_persists_broker_order_id(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    event = make_event()
    verdict = make_verdict(direction=Direction.LONG)
    decision = GateDecision(plan=make_plan(), reason="entry approved")
    row_id = store.record(event, verdict, decision)

    store.update_order_id(row_id, "abc-broker-order-123")

    row = store.recent(1)[0]
    assert row["broker_order_id"] == "abc-broker-order-123"


def test_update_order_id_unknown_id_raises(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")

    with pytest.raises(ValueError):
        store.update_order_id(999, "some-order-id")
