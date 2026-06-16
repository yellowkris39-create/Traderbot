"""Tests for brokebyte.memory.calibration (Module 7 calibration layer)."""
from brokebyte.fusion.context import TradeProposal
from brokebyte.guards.regime import Regime, Trend
from brokebyte.ingestion.events import NewsEvent
from brokebyte.llm.provider import Direction, LLMVerdict, TimeHorizon
from brokebyte.memory.calibration import (
    MIN_CALIBRATION_SAMPLE,
    BucketStats,
    CalibrationResult,
    compute_calibration,
)
from brokebyte.memory.store import DecisionStore
from brokebyte.risk.gate import GateDecision
from brokebyte.risk.sizing import PositionPlan


def make_event():
    return NewsEvent(id="evt-1", headline="H", summary="S", symbols=["AAPL"], source="test")


def make_verdict(direction=Direction.LONG, confidence=0.75):
    return LLMVerdict(
        material=True, symbol="AAPL", direction=direction, confidence=confidence,
        time_horizon=TimeHorizon.SWING, reasoning="r", is_already_priced_in=False,
    )


def make_plan():
    return PositionPlan(
        symbol="AAPL", side="buy", qty=10, entry_price=100.0,
        stop_price=96.0, take_profit_price=108.0, risk_amount=40.0, notional=1000.0,
    )


def make_enter_decision(trend: Trend, confidence: float, pnl: float, store: DecisionStore) -> None:
    verdict = make_verdict(confidence=confidence)
    proposal = TradeProposal(
        verdict=verdict,
        regime=Regime(trend=trend, high_volatility=False, size_multiplier=1.0),
        support=80.0,
        resistance=120.0,
    )
    decision = GateDecision(plan=make_plan(), reason="enter", proposal=proposal)
    row_id = store.record(make_event(), verdict, decision)
    store.record_outcome(row_id, exit_price=108.0, exit_reason="take_profit", pnl=pnl)


# --- empty store --------------------------------------------------------------


def test_calibration_empty_store_has_no_regime_stats(tmp_path):
    store = DecisionStore(tmp_path / "d.db")

    cal = compute_calibration(store)

    assert cal.by_regime == {}


def test_calibration_empty_store_has_all_confidence_buckets_at_zero(tmp_path):
    store = DecisionStore(tmp_path / "d.db")

    cal = compute_calibration(store)

    for stats in cal.by_confidence.values():
        assert stats.count == 0
        assert stats.win_rate == 0.0
        assert stats.mean_pnl == 0.0


def test_calibration_empty_store_insufficient_data(tmp_path):
    store = DecisionStore(tmp_path / "d.db")

    cal = compute_calibration(store)

    assert cal.sufficient_data is False


# --- by_regime grouping -------------------------------------------------------


def test_calibration_by_regime_groups_correctly(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    make_enter_decision(Trend.UP, 0.75, 50.0, store)
    make_enter_decision(Trend.UP, 0.75, -20.0, store)
    make_enter_decision(Trend.DOWN, 0.6, 30.0, store)

    cal = compute_calibration(store)

    assert "up" in cal.by_regime
    assert "down" in cal.by_regime
    assert cal.by_regime["up"].count == 2
    assert cal.by_regime["down"].count == 1


def test_calibration_by_regime_win_rate(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    make_enter_decision(Trend.UP, 0.75, 50.0, store)
    make_enter_decision(Trend.UP, 0.75, -20.0, store)

    cal = compute_calibration(store)

    assert cal.by_regime["up"].win_rate == pytest.approx(0.5)


def test_calibration_by_regime_mean_pnl(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    make_enter_decision(Trend.UP, 0.75, 40.0, store)
    make_enter_decision(Trend.UP, 0.75, 60.0, store)

    cal = compute_calibration(store)

    assert cal.by_regime["up"].mean_pnl == pytest.approx(50.0)


# --- by_confidence grouping ---------------------------------------------------


def test_calibration_by_confidence_routes_to_correct_bucket(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    make_enter_decision(Trend.UP, 0.3, 10.0, store)   # bucket 0.0-0.5
    make_enter_decision(Trend.UP, 0.6, 10.0, store)   # bucket 0.5-0.7
    make_enter_decision(Trend.UP, 0.8, 10.0, store)   # bucket 0.7-0.85
    make_enter_decision(Trend.UP, 0.9, 10.0, store)   # bucket 0.85-1.0

    cal = compute_calibration(store)

    assert cal.by_confidence["0.0-0.5"].count == 1
    assert cal.by_confidence["0.5-0.7"].count == 1
    assert cal.by_confidence["0.7-0.85"].count == 1
    assert cal.by_confidence["0.85-1.0"].count == 1


def test_calibration_by_confidence_empty_buckets_present(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    make_enter_decision(Trend.UP, 0.9, 10.0, store)  # only top bucket

    cal = compute_calibration(store)

    # All four bucket labels always present
    assert set(cal.by_confidence.keys()) == {"0.0-0.5", "0.5-0.7", "0.7-0.85", "0.85-1.0"}
    assert cal.by_confidence["0.0-0.5"].count == 0


# --- sufficient_data flag -----------------------------------------------------


def test_calibration_sufficient_data_false_when_all_buckets_low(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    for _ in range(MIN_CALIBRATION_SAMPLE - 1):
        make_enter_decision(Trend.UP, 0.75, 10.0, store)

    cal = compute_calibration(store)

    assert cal.sufficient_data is False


def test_calibration_sufficient_data_true_when_one_bucket_reaches_threshold(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    for _ in range(MIN_CALIBRATION_SAMPLE):
        make_enter_decision(Trend.UP, 0.75, 10.0, store)

    cal = compute_calibration(store)

    assert cal.sufficient_data is True


# --- HOLD decisions excluded --------------------------------------------------


def test_calibration_ignores_hold_decisions(tmp_path):
    store = DecisionStore(tmp_path / "d.db")
    event = make_event()
    verdict = make_verdict()
    for _ in range(5):
        store.record(event, verdict, GateDecision(plan=None, reason="hold"))

    cal = compute_calibration(store)

    assert cal.by_regime == {}
    assert all(s.count == 0 for s in cal.by_confidence.values())


import pytest
