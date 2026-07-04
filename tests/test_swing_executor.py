"""Tests for the swing auto-executor (screener signals -> Alpaca brackets)."""

from datetime import datetime, timedelta, timezone

import pytest

from brokebyte.memory.store import DecisionStore
from brokebyte.screener import executor as ex
from brokebyte.screener.screen import ScreenResult
from brokebyte.screener.sizing_gbp import GbpTradePlan


NOW = datetime(2026, 7, 6, 13, 40, tzinfo=timezone.utc)


class _FakeOrder:
    def __init__(self):
        self.id = "ord-swing-1"


class _FakeBroker:
    def __init__(self, prices, held=None, submit_raises=False):
        self._prices = prices
        self._held = set(held or [])
        self._raises = submit_raises
        self.submitted = []

    def get_position_symbols(self):
        return set(self._held)

    def get_current_price(self, symbol):
        return self._prices.get(symbol)

    def submit_bracket_order(self, plan):
        if self._raises:
            raise RuntimeError("rejected")
        self.submitted.append(plan)
        return _FakeOrder()


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


def _store(tmp_path):
    return DecisionStore(tmp_path / "d.db")


def _sig(symbol="AAPL", stop=95.0, ts=None):
    return {"symbol": symbol, "signal_close": 100.0, "stop": stop,
            "ts": (ts or NOW - timedelta(hours=16)).isoformat()}


# --- sizing -------------------------------------------------------------------

def test_size_swing_risk_cap():
    plan = ex.size_swing("AAPL", 100.0, 95.0)  # $100 risk / $5 per share = 20
    assert plan.qty == 20 and plan.take_profit_price == 110.0
    assert plan.risk_amount == 100.0


def test_size_swing_exposure_cap_wins():
    plan = ex.size_swing("PRICY", 150.0, 149.0)  # risk cap 100 sh, exposure 2000/150=13
    assert plan.qty == 13


def test_size_swing_unsizeable_returns_none():
    assert ex.size_swing("HUGE", 2500.0, 2400.0) is None  # 1 share breaches $2k cap
    assert ex.size_swing("BAD", 100.0, 101.0) is None     # stop above entry


# --- pending file --------------------------------------------------------------

def _result(symbol, passed=True):
    plan = GbpTradePlan(shares=1.0, entry_price=100.0, stop_price=95.0,
                        take_profit_price=110.0, risk_per_share=5.0,
                        risk_amount=5.0, notional=100.0, exposure_capped=False)
    return ScreenResult(symbol, passed, price=100.0, plan=plan)


def test_write_pending_filters_lse(tmp_path):
    path = tmp_path / "pending.jsonl"
    n = ex.write_pending([_result("AAPL"), _result("SHEL.L")], path, now=NOW)
    assert n == 1
    fresh, stale = ex.load_pending(path, now=NOW)
    assert [s["symbol"] for s in fresh] == ["AAPL"] and stale == 0


def test_load_pending_drops_stale(tmp_path):
    path = tmp_path / "pending.jsonl"
    ex.write_pending([_result("AAPL")], path, now=NOW - timedelta(hours=40))
    ex.write_pending([_result("MSFT")], path, now=NOW - timedelta(hours=16))
    fresh, stale = ex.load_pending(path, now=NOW)
    assert [s["symbol"] for s in fresh] == ["MSFT"] and stale == 1


# --- execution guards -----------------------------------------------------------

def test_happy_path_submits_records_and_tags_strategy(tmp_path):
    store = _store(tmp_path)
    broker = _FakeBroker({"AAPL": 100.0})
    res = ex.execute_pending(broker, store, [_sig()], _Log(), now=NOW)
    assert len(res.executed) == 1 and not res.breaker_tripped
    assert broker.submitted[0].qty == 20
    rows = store.open_enter_decisions()
    assert len(rows) == 1
    assert rows[0]["strategy"] == "swing"
    assert rows[0]["broker_order_id"] == "ord-swing-1"


def test_skips_symbol_already_held(tmp_path):
    store = _store(tmp_path)
    broker = _FakeBroker({"AAPL": 100.0}, held={"AAPL"})
    res = ex.execute_pending(broker, store, [_sig()], _Log(), now=NOW)
    assert not res.executed and res.skipped[0][1] == "symbol already held"


def test_skips_gap_below_stop(tmp_path):
    store = _store(tmp_path)
    broker = _FakeBroker({"AAPL": 94.0})
    res = ex.execute_pending(broker, store, [_sig(stop=95.0)], _Log(), now=NOW)
    assert not res.executed and "gapped below stop" in res.skipped[0][1]


def test_max_three_open_swing_positions(tmp_path):
    store = _store(tmp_path)
    for sym in ("A", "B", "C"):
        store.record_swing_entry(symbol=sym, side="buy", qty=1, entry_price=10.0, stop_price=9.0, take_profit_price=12.0, risk_amount=1.0, notional=10.0, reason="t")
    broker = _FakeBroker({"AAPL": 100.0})
    res = ex.execute_pending(broker, store, [_sig()], _Log(), now=NOW)
    assert not res.executed and "max 3 open" in res.skipped[0][1]


def test_breaker_blocks_after_three_consecutive_losses(tmp_path):
    store = _store(tmp_path)
    for i, sym in enumerate(("A", "B", "C")):
        did = store.record_swing_entry(symbol=sym, side="buy", qty=1, entry_price=10.0, stop_price=9.0, take_profit_price=12.0, risk_amount=1.0, notional=10.0, reason="t")
        store.record_outcome(did, exit_price=9.0, exit_reason="stop", pnl=-1.0, closed_at=NOW - timedelta(days=3 - i))
    broker = _FakeBroker({"AAPL": 100.0})
    res = ex.execute_pending(broker, store, [_sig()], _Log(), now=NOW)
    assert res.breaker_tripped and not res.executed and not broker.submitted
    assert "CIRCUIT BREAKER" in ex.format_execution_report(res)


def test_win_resets_breaker(tmp_path):
    store = _store(tmp_path)
    pnls = [-1.0, -1.0, -1.0, 2.0]
    for i, pnl in enumerate(pnls):
        did = store.record_swing_entry(symbol="S{}".format(i), side="buy", qty=1, entry_price=10.0, stop_price=9.0, take_profit_price=12.0, risk_amount=1.0, notional=10.0, reason="t")
        store.record_outcome(did, exit_price=10.0 + pnl, exit_reason="x", pnl=pnl, closed_at=NOW - timedelta(days=len(pnls) - i))
    assert store.consecutive_swing_losses() == 0
    broker = _FakeBroker({"AAPL": 100.0})
    res = ex.execute_pending(broker, store, [_sig()], _Log(), now=NOW)
    assert len(res.executed) == 1


def test_submission_failure_downgrades_row(tmp_path):
    store = _store(tmp_path)
    broker = _FakeBroker({"AAPL": 100.0}, submit_raises=True)
    res = ex.execute_pending(broker, store, [_sig()], _Log(), now=NOW)
    assert not res.executed
    assert store.open_enter_decisions() == []  # no phantom open ENTER
    assert store.open_swing_count() == 0


# --- exit manager integration ----------------------------------------------------

def test_swing_rows_get_20_day_time_stop():
    from brokebyte.monitor.exit_manager import NEWS_MAX_HOLDING_DAYS, SWING_MAX_HOLDING_DAYS, _row_strategy
    assert NEWS_MAX_HOLDING_DAYS == 10 and SWING_MAX_HOLDING_DAYS == 20
    assert _row_strategy({"strategy": "swing"}) == "swing"
    assert _row_strategy({"strategy": None}) == "news"
    assert _row_strategy({}) == "news"
