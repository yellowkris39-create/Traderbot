from brokebyte.backtest.metrics import compute_metrics
from brokebyte.ingestion.events import NewsEvent
from brokebyte.llm.provider import Direction, LLMVerdict, TimeHorizon
from brokebyte.memory.metrics import compute_decision_store_metrics
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


def test_empty_store_returns_empty_metrics(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")

    metrics = compute_decision_store_metrics(store)

    assert metrics.trade_count == 0
    assert metrics.sortino_ratio is None


def test_metrics_match_compute_metrics_over_closed_trades(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    event = make_event()
    verdict = make_verdict()

    id1 = store.record(event, verdict, GateDecision(plan=make_plan(), reason="enter 1"))
    id2 = store.record(event, verdict, GateDecision(plan=make_plan(), reason="enter 2"))
    store.record(event, verdict, GateDecision(plan=None, reason="hold, no outcome"))

    store.record_outcome(id1, exit_price=108.0, exit_reason="take_profit", pnl=800.0)
    store.record_outcome(id2, exit_price=96.0, exit_reason="stop", pnl=-400.0)

    metrics = compute_decision_store_metrics(store, initial_equity=50_000.0)

    assert metrics == compute_metrics([800.0, -400.0], 50_000.0)
    assert metrics.trade_count == 2
