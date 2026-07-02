"""Tests for the paper-trade journal + evaluator (the demo's scorekeeper)."""

import pytest

from brokebyte.screener import journal as j


def _mk(symbol="AAPL", entry=100.0, stop=95.0, **kw):
    trades = kw.pop("trades", [])
    return trades, j.open_trade(trades, symbol, entry, stop, **kw)


# --- open ------------------------------------------------------------------

def test_open_defaults_to_2r_target():
    _, t = _mk(entry=100.0, stop=95.0, date="2026-07-02")
    assert t["target"] == 110.0  # entry + 2 * (100-95)
    assert t["id"] == "AAPL-20260702"
    assert t["status"] == "open"


def test_open_rejects_stop_at_or_above_entry():
    with pytest.raises(ValueError):
        j.open_trade([], "AAPL", 100.0, 100.0)
    with pytest.raises(ValueError):
        j.open_trade([], "AAPL", 100.0, 101.0)


def test_open_rejects_duplicate_id():
    trades, _ = _mk(date="2026-07-02")
    with pytest.raises(ValueError, match="already exists"):
        j.open_trade(trades, "AAPL", 101.0, 96.0, date="2026-07-02")


# --- close / realized R ------------------------------------------------------

def test_close_computes_r_win():
    trades, _ = _mk(entry=100.0, stop=95.0, date="2026-07-02")
    t = j.close_trade(trades, "AAPL", 110.0, reason="target", date="2026-07-10")
    assert t["r"] == 2.0
    assert t["status"] == "closed"


def test_close_computes_r_loss():
    trades, _ = _mk(entry=100.0, stop=95.0)
    t = j.close_trade(trades, "AAPL", 95.0, reason="stop")
    assert t["r"] == -1.0


def test_close_by_symbol_ambiguous_requires_id():
    trades, _ = _mk(date="2026-07-01")
    j.open_trade(trades, "AAPL", 102.0, 97.0, date="2026-07-02")
    with pytest.raises(ValueError, match="ambiguous"):
        j.close_trade(trades, "AAPL", 105.0)
    t = j.close_trade(trades, "AAPL-20260701", 105.0)
    assert t["id"] == "AAPL-20260701"


def test_close_missing_raises():
    with pytest.raises(ValueError, match="no open trade"):
        j.close_trade([], "TSLA", 100.0)


# --- streak & circuit breaker ------------------------------------------------

def _closed(sym, day, r):
    return {"id": f"{sym}-202607{day:02d}", "symbol": sym, "status": "closed",
            "opened": f"2026-07-{day:02d}", "closed": f"2026-07-{day:02d}",
            "entry": 100.0, "stop": 95.0, "exit": 100.0 + 5 * r, "r": r,
            "target": 110.0, "exit_reason": "test"}


def test_loss_streak_counts_trailing_losses_only():
    closed = [_closed("A", 1, 2.0), _closed("B", 2, -1.0), _closed("C", 3, -1.0)]
    assert j.loss_streak(closed) == 2


def test_breakeven_resets_streak():
    closed = [_closed("A", 1, -1.0), _closed("B", 2, -1.0), _closed("C", 3, 0.0)]
    assert j.loss_streak(closed) == 0


def test_breaker_trips_at_three_losses():
    closed = [_closed(s, d, -1.0) for s, d in (("A", 1), ("B", 2), ("C", 3))]
    e = j.evaluate(closed)
    assert e["loss_streak"] == 3 and e["breaker_tripped"]
    assert "CIRCUIT BREAKER" in j.format_report(closed)


# --- report ------------------------------------------------------------------

def test_report_small_sample_refuses_benchmark_verdict():
    closed = [_closed("A", 1, 2.0), _closed("B", 2, -1.0)]
    rep = j.format_report(closed)
    assert "too few to compare" in rep
    assert "BELOW benchmark" not in rep and "ABOVE benchmark" not in rep


def test_report_full_sample_compares_to_benchmark():
    closed = [_closed(chr(65 + i), i + 1, 2.0 if i % 2 == 0 else -1.0) for i in range(16)]
    rep = j.format_report(closed)
    assert "benchmark" in rep and ("AT/ABOVE benchmark" in rep or "BELOW benchmark" in rep)
    assert "not proof" in rep


def test_report_warns_over_max_open():
    trades = []
    for i, s in enumerate(("A", "B", "C", "D")):
        j.open_trade(trades, s, 100.0, 95.0, date=f"2026-07-0{i+1}")
    assert "exceeds the max" in j.format_report(trades)


# --- persistence round-trip ---------------------------------------------------

def test_save_load_roundtrip(tmp_path):
    trades, _ = _mk(date="2026-07-02")
    j.close_trade(trades, "AAPL", 110.0, reason="target", date="2026-07-05")
    p = tmp_path / "paper_trades.jsonl"
    j.save_trades(p, trades)
    assert j.load_trades(p) == trades


def test_cli_open_list_close_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    j.main(["open", "AAPL", "100", "95", "--date=2026-07-02"])
    j.main(["list"])
    j.main(["close", "AAPL", "110", "--reason=target", "--date=2026-07-10"])
    j.main(["report"])
    out = capsys.readouterr().out
    assert "opened AAPL-20260702" in out
    assert "+2.000R" in out
    assert "PAPER DEMO REPORT" in out
