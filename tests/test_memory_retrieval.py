"""Tests for brokebyte.memory.retrieval (Module 7 retrieval layer)."""
import sqlite3

import pytest

from brokebyte.memory.retrieval import MIN_RETRIEVAL_SAMPLE, format_similar_setups, retrieve_similar
from brokebyte.memory.store import DecisionStore
from brokebyte.fusion.context import TradeProposal
from brokebyte.guards.regime import Regime, Trend
from brokebyte.ingestion.events import NewsEvent
from brokebyte.llm.provider import Direction, LLMVerdict, TimeHorizon
from brokebyte.risk.gate import GateDecision
from brokebyte.risk.sizing import PositionPlan


def make_event(**overrides):
    defaults = dict(
        id="evt-1",
        headline="Corp announces product",
        summary="Summary.",
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
        confidence=0.75,
        time_horizon=TimeHorizon.SWING,
        reasoning="test",
        is_already_priced_in=False,
    )
    defaults.update(overrides)
    return LLMVerdict(**defaults)


def make_plan(**overrides):
    defaults = dict(
        symbol="AAPL", side="buy", qty=10, entry_price=100.0,
        stop_price=96.0, take_profit_price=108.0, risk_amount=40.0, notional=1000.0,
    )
    defaults.update(overrides)
    return PositionPlan(**defaults)


def make_enter_proposal(trend: Trend = Trend.UP):
    verdict = make_verdict()
    return TradeProposal(
        verdict=verdict,
        regime=Regime(trend=trend, high_volatility=False, size_multiplier=1.0),
        support=80.0,
        resistance=120.0,
    )


def seed_closed_enters(store: DecisionStore, n: int, trend: Trend = Trend.UP, pnl: float = 50.0) -> list[int]:
    """Insert n closed ENTER decisions with the given regime and pnl."""
    ids = []
    event = make_event()
    verdict = make_verdict()
    proposal = make_enter_proposal(trend)
    for i in range(n):
        decision = GateDecision(plan=make_plan(), reason=f"enter {i}", proposal=proposal)
        row_id = store.record(event, verdict, decision)
        store.record_outcome(row_id, exit_price=108.0, exit_reason="take_profit", pnl=pnl)
        ids.append(row_id)
    return ids


# --- retrieve_similar ---------------------------------------------------------


def test_retrieve_similar_returns_empty_below_threshold(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    seed_closed_enters(store, MIN_RETRIEVAL_SAMPLE - 1)

    result = retrieve_similar(store, "up", k=5)

    assert result == []


def test_retrieve_similar_returns_rows_at_threshold(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    seed_closed_enters(store, MIN_RETRIEVAL_SAMPLE, trend=Trend.UP)

    result = retrieve_similar(store, "up", k=5)

    assert len(result) == MIN_RETRIEVAL_SAMPLE


def test_retrieve_similar_filters_by_regime(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    seed_closed_enters(store, MIN_RETRIEVAL_SAMPLE, trend=Trend.UP)
    seed_closed_enters(store, MIN_RETRIEVAL_SAMPLE, trend=Trend.DOWN)

    up_results = retrieve_similar(store, "up", k=20)
    down_results = retrieve_similar(store, "down", k=20)

    assert all(row["regime_trend"] == "up" for row in up_results)
    assert all(row["regime_trend"] == "down" for row in down_results)


def test_retrieve_similar_caps_at_k(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    seed_closed_enters(store, 10, trend=Trend.UP)

    result = retrieve_similar(store, "up", k=3)

    assert len(result) == 3


def test_retrieve_similar_only_includes_closed_enters(tmp_path):
    store = DecisionStore(tmp_path / "decisions.db")
    event = make_event()
    verdict = make_verdict()
    proposal = make_enter_proposal(Trend.UP)

    # Unclosed ENTER (no outcome recorded)
    for _ in range(3):
        store.record(event, verdict, GateDecision(plan=make_plan(), reason="open enter", proposal=proposal))

    # HOLD decisions (never get pnl)
    for _ in range(3):
        store.record(event, verdict, GateDecision(plan=None, reason="hold", proposal=proposal))

    # Enough closed ENTERs to pass the guard
    seed_closed_enters(store, MIN_RETRIEVAL_SAMPLE, trend=Trend.UP)

    result = retrieve_similar(store, "up", k=20)

    assert all(row["action"] == "ENTER" for row in result)
    assert all(row["pnl"] is not None for row in result)


# --- format_similar_setups ----------------------------------------------------


def test_format_similar_setups_returns_empty_string_for_no_rows():
    assert format_similar_setups([]) == ""


def _make_row(recorded_at="2026-01-15T12:00:00+00:00", direction="long",
              regime="up", confidence=0.75, exit_reason="take_profit", pnl=80.0) -> sqlite3.Row:
    """Build a real sqlite3.Row using an in-memory DB."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE r (recorded_at TEXT, verdict_direction TEXT, "
        "regime_trend TEXT, verdict_confidence REAL, exit_reason TEXT, pnl REAL)"
    )
    conn.execute(
        "INSERT INTO r VALUES (?, ?, ?, ?, ?, ?)",
        (recorded_at, direction, regime, confidence, exit_reason, pnl),
    )
    return conn.execute("SELECT * FROM r").fetchone()


def test_format_similar_setups_includes_structured_fields():
    row = _make_row(recorded_at="2026-01-15T12:00:00+00:00", direction="long",
                    regime="up", confidence=0.75, exit_reason="take_profit", pnl=80.0)

    text = format_similar_setups([row])

    assert "2026-01-15" in text
    assert "direction=long" in text
    assert "regime=up" in text
    assert "confidence=0.75" in text
    assert "take_profit" in text
    assert "+80.00" in text


def test_format_similar_setups_header_shows_count():
    rows = [_make_row() for _ in range(3)]

    text = format_similar_setups(rows)

    assert "3 most recent" in text


def test_format_similar_setups_shows_no_outcome_when_pnl_is_none():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE r (recorded_at TEXT, verdict_direction TEXT, "
        "regime_trend TEXT, verdict_confidence REAL, exit_reason TEXT, pnl REAL)"
    )
    conn.execute("INSERT INTO r VALUES ('2026-01-01T00:00:00+00:00', 'long', 'up', 0.8, NULL, NULL)")
    row = conn.execute("SELECT * FROM r").fetchone()

    text = format_similar_setups([row])

    assert "no outcome recorded" in text
