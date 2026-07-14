"""Tests for the post-gate execution flow in brokebyte.main._process_event.

Focus: an approved ENTER that is subsequently blocked (duplicate / market
closed) or fails to submit must NOT be left in the decision store as an open
ENTER decision — otherwise the reconciler treats it as a forever-open
"phantom" position it can never close.  These tests pin the fix that folds the
duplicate-order and market-hours checks ahead of memory.record() and downgrades
a persisted ENTER when submission fails.
"""

from unittest.mock import MagicMock, patch

import pandas as pd

import pytest

import brokebyte.main as main


@pytest.fixture(autouse=True)
def _news_entries_enabled(monkeypatch):
    """These tests exercise the news-bot ENTRY path, which is paused by
    default since 2026-07-04 (NEWS_ENTRIES_ENABLED). Enable it here; the
    pause behaviour itself is covered by test_news_entries_paused_*."""
    monkeypatch.setenv("NEWS_ENTRIES_ENABLED", "true")
    monkeypatch.setenv("NEWS_PIPELINE_ENABLED", "true")
from brokebyte.common import Quote
from brokebyte.ingestion.events import NewsEvent
from brokebyte.llm.provider import Direction, LLMVerdict, TimeHorizon
from brokebyte.memory.store import DecisionStore
from brokebyte.risk import gate
from brokebyte.risk.limits import load_risk_limits
from brokebyte.risk.sizing import PositionPlan


# --- fakes -------------------------------------------------------------------

class _FakeOrder:
    def __init__(self, symbol: str) -> None:
        self.id = "ord-123"
        self.symbol = symbol
        self.side = "buy"
        self.status = "accepted"


class _FakeBroker:
    def __init__(self, market_open: bool = True, submit_raises: bool = False) -> None:
        self._market_open = market_open
        self._submit_raises = submit_raises
        self.submit_called = 0

    def get_account_summary(self) -> dict:
        return {
            "account_id": "x", "status": "ACTIVE",
            "cash": "100000", "equity": "100000", "last_equity": "100000",
            "portfolio_value": "100000", "buying_power": "100000",
            "shorting_enabled": True,
        }

    def get_positions(self) -> list[dict]:
        return []

    def is_market_open(self) -> bool:
        return self._market_open

    def submit_bracket_order(self, plan: PositionPlan):
        self.submit_called += 1
        if self._submit_raises:
            raise RuntimeError("broker rejected order")
        return _FakeOrder(plan.symbol)

    def kill_switch(self, reason: str):  # pragma: no cover - not exercised here
        raise AssertionError("kill switch should not fire in these tests")


class _FakeMarketData:
    def get_daily_bars(self, symbol: str) -> pd.DataFrame:
        return pd.DataFrame()

    def get_quote(self, symbol: str) -> Quote:
        return Quote(bid_price=10.0, ask_price=10.02)


class _FakeProvider:
    def evaluate(self, event, historical_context: str = ""):
        return LLMVerdict(
            material=True,
            symbol="AAPL",
            direction=Direction.LONG,
            confidence=0.9,
            time_horizon=TimeHorizon.SWING,
            reasoning="bullish catalyst",
            is_already_priced_in=False,
        )


class _FakeCircuitBreaker:
    def __init__(self) -> None:
        self.errors = 0
        self.successes = 0
        self.trades = 0

    def record_error(self) -> None:
        self.errors += 1

    def record_success(self) -> None:
        self.successes += 1

    def record_trade(self) -> None:
        self.trades += 1


def _enter_plan(symbol: str = "AAPL") -> PositionPlan:
    return PositionPlan(
        symbol=symbol, side="buy", qty=10, entry_price=10.0,
        stop_price=9.0, take_profit_price=12.0, risk_amount=10.0, notional=100.0,
    )


def _run(memory, broker, active_symbols, *, enter: bool):
    """Drive one event through _process_event with the gate forced to return
    an ENTER (enter=True) or a plain HOLD (enter=False)."""
    event = NewsEvent(id="evt-1", headline="AAPL beats", summary="s", symbols=["AAPL"], source="test")
    decision = (
        gate.GateDecision(plan=_enter_plan(), reason="entry approved", proposal=None)
        if enter
        else gate.GateDecision(plan=None, reason="not material", proposal=None)
    )
    with patch.object(main, "classify_regime", return_value=MagicMock()), \
         patch.object(main, "retrieve_similar", return_value=[]), \
         patch.object(main, "format_similar_setups", return_value=""), \
         patch.object(main.gate, "evaluate", return_value=decision):
        main._process_event(
            event,
            broker,
            _FakeMarketData(),
            _FakeProvider(),
            _FakeCircuitBreaker(),
            memory,
            load_risk_limits(),
            main.get_logger("test"),
            main._PortfolioCache(ttl=30),
            active_symbols,
        )


# --- tests -------------------------------------------------------------------

def test_market_closed_records_hold_not_open_enter(tmp_path):
    memory = DecisionStore(tmp_path / "d.db")
    broker = _FakeBroker(market_open=False)
    _run(memory, broker, set(), enter=True)

    assert broker.submit_called == 0
    assert memory.open_enter_decisions() == []  # no phantom open ENTER
    last = memory.recent(1)[0]
    assert last["action"] == "HOLD"
    assert "market is closed" in last["reason"]


def test_duplicate_symbol_records_hold_not_open_enter(tmp_path):
    memory = DecisionStore(tmp_path / "d.db")
    broker = _FakeBroker(market_open=True)
    _run(memory, broker, {"AAPL"}, enter=True)

    assert broker.submit_called == 0
    assert memory.open_enter_decisions() == []
    last = memory.recent(1)[0]
    assert last["action"] == "HOLD"
    assert "duplicate order blocked" in last["reason"]


def test_submission_failure_downgrades_persisted_enter(tmp_path):
    memory = DecisionStore(tmp_path / "d.db")
    broker = _FakeBroker(market_open=True, submit_raises=True)
    _run(memory, broker, set(), enter=True)

    assert broker.submit_called == 1
    # The ENTER was persisted then downgraded — must not remain open.
    assert memory.open_enter_decisions() == []
    last = memory.recent(1)[0]
    assert last["action"] == "HOLD"
    assert "order submission failed" in last["reason"]


def test_happy_path_records_open_enter_with_order_id(tmp_path):
    memory = DecisionStore(tmp_path / "d.db")
    broker = _FakeBroker(market_open=True)
    active: set[str] = set()
    _run(memory, broker, active, enter=True)

    assert broker.submit_called == 1
    assert "AAPL" in active
    open_rows = memory.open_enter_decisions()
    assert len(open_rows) == 1
    assert open_rows[0]["action"] == "ENTER"
    assert open_rows[0]["broker_order_id"] == "ord-123"


def test_market_closed_check_skipped_for_plain_hold(tmp_path):
    """A gate HOLD is recorded as HOLD regardless of market state and never
    touches the broker submit path."""
    memory = DecisionStore(tmp_path / "d.db")
    broker = _FakeBroker(market_open=False)
    _run(memory, broker, set(), enter=False)

    assert broker.submit_called == 0
    assert memory.open_enter_decisions() == []
    assert memory.recent(1)[0]["action"] == "HOLD"

def test_news_entries_paused_by_default_records_hold(tmp_path, monkeypatch):
    monkeypatch.delenv("NEWS_ENTRIES_ENABLED", raising=False)
    memory = DecisionStore(tmp_path / "d.db")
    broker = _FakeBroker(market_open=True)
    _run(memory, broker, set(), enter=True)

    assert broker.submit_called == 0
    assert memory.open_enter_decisions() == []
    last = memory.recent(1)[0]
    assert last["action"] == "HOLD"
    assert "paused" in last["reason"]



def test_news_pipeline_disabled_makes_no_llm_call_and_no_db_row(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_PIPELINE_ENABLED", "false")
    memory = DecisionStore(tmp_path / "d.db")
    broker = _FakeBroker(market_open=True)

    class _ExplodingProvider:
        def evaluate(self, event, historical_context=""):
            raise AssertionError("LLM must not be called when pipeline disabled")

    event = NewsEvent(id="evt-off", headline="X", summary="s", symbols=["AAPL"], source="test")
    main._process_event(
        event, broker, _FakeMarketData(), _ExplodingProvider(), _FakeCircuitBreaker(),
        memory, load_risk_limits(), main.get_logger("test"),
        main._PortfolioCache(ttl=30), set(),
    )
    assert memory.count() == 0
    assert broker.submit_called == 0
