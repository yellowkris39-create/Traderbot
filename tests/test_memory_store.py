from brokebyte.fusion.context import TradeProposal
from brokebyte.guards.regime import Regime, Trend
from brokebyte.ingestion.events import NewsEvent
from brokebyte.llm.provider import Direction, LLMVerdict, TimeHorizon
from brokebyte.memory.store import DecisionStore
from brokebyte.risk.gate import GateDecision
from brokebyte.risk.sizing import PositionPlan


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
