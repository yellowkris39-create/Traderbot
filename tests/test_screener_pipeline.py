"""Tests for the yfinance provider parsing, screen orchestration, and alerts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from brokebyte.screener import screen, alerts
from brokebyte.screener.data import Fundamentals
from brokebyte.screener.screen import ScreenResult, Screener, evaluate_symbol
from brokebyte.screener.sizing_gbp import size_trade_gbp
from brokebyte.screener import yfinance_provider as yp


# ----------------------- yfinance parsing (pure) -----------------------

def test_normalize_bars_lowercases_and_orders():
    idx = pd.date_range("2026-01-01", periods=3, freq="D")
    raw = pd.DataFrame(
        {"Open": [1, 2, 3], "High": [2, 3, 4], "Low": [0.5, 1, 2],
         "Close": [1.5, 2.5, 3.5], "Volume": [10, 20, 30], "Dividends": [0, 0, 0]},
        index=idx,
    )
    out = yp.normalize_bars(raw, "USD")
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert out["close"].iloc[-1] == 3.5


def test_normalize_bars_pence_to_pounds():
    idx = pd.date_range("2026-01-01", periods=2, freq="D")
    raw = pd.DataFrame(
        {"Open": [1000, 1100], "High": [1050, 1150], "Low": [990, 1090],
         "Close": [1020, 1120], "Volume": [5, 6]}, index=idx)
    out = yp.normalize_bars(raw, "GBp")
    assert out["close"].iloc[-1] == pytest.approx(11.20)   # 1120 pence -> £11.20
    assert out["volume"].iloc[-1] == 6                      # volume untouched


def test_normalize_bars_empty_and_missing_cols():
    assert yp.normalize_bars(pd.DataFrame(), "USD").empty
    bad = pd.DataFrame({"Open": [1], "Close": [1]})
    assert yp.normalize_bars(bad, "USD").empty


def test_extract_fundamentals_helpers():
    fast = {"currency": "USD", "market_cap": 2_000_000_000}
    assert yp.extract_currency(fast) == "USD"
    assert yp.extract_market_cap(fast) == 2_000_000_000.0
    assert yp.extract_beta({"beta": 1.2}) == 1.2
    assert yp.extract_beta({}) is None


def test_extract_next_earnings_picks_soonest_future():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    cal = {"Earnings Date": [datetime(2026, 5, 1), datetime(2026, 6, 10), datetime(2026, 7, 1)]}
    nxt = yp.extract_next_earnings(cal, now=now)
    assert nxt.date() == datetime(2026, 6, 10).date()


# ----------------------- screen helpers (pure) -----------------------

def _trend_bars(n=220, start=60.0, end=108.0):
    closes = [start + (end - start) * i / (n - 1) for i in range(n)]
    return pd.DataFrame({
        "open": closes, "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes], "close": closes,
        "volume": [1_000_000] * n,
    })


def test_index_symbol_for():
    assert screen.index_symbol_for("SHEL.L") == "^FTSE"
    assert screen.index_symbol_for("AAPL") == "SPY"


def test_pullback_and_swing_low():
    b = _trend_bars()
    # drop the last close 7% below recent high to simulate a pullback
    b.loc[b.index[-1], "close"] = 108.0 * 0.93
    assert 0.03 <= screen.pullback_pct(b) <= 0.10
    assert screen.swing_low(b) <= b["low"].iloc[-1] + 1


def test_evaluate_symbol_insufficient_history():
    short = _trend_bars(n=50)
    res = evaluate_symbol("AAPL", short, _trend_bars(), Fundamentals(2e9, 1.1, None, "USD"))
    assert not res.passed and "insufficient" in res.failures[0]


def test_evaluate_symbol_downtrend_fails_trend():
    down = _trend_bars(start=120, end=60)  # falling -> 50SMA<200SMA fails
    res = evaluate_symbol("AAPL", down, _trend_bars(),
                          Fundamentals(2e9, 1.1, datetime(2030, 1, 1, tzinfo=timezone.utc), "USD"))
    assert not res.passed


# ----------------------- Screener.scan orchestration -----------------------

class _FakeProvider:
    def __init__(self, stock_bars, index_bars, funds):
        self._stock, self._index, self._funds = stock_bars, index_bars, funds

    def daily_bars(self, symbol, lookback_days=400):
        return self._index if symbol in ("SPY", "^FTSE") else self._stock

    def fundamentals(self, symbol):
        return self._funds


def test_scan_skips_when_index_below_200sma(monkeypatch):
    # index in a downtrend -> regime gate closes -> evaluate never reached
    index = _trend_bars(start=120, end=60)
    prov = _FakeProvider(_trend_bars(), index, Fundamentals(2e9, 1.1, None, "USD"))
    called = {"n": 0}
    monkeypatch.setattr(screen, "evaluate_symbol",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or ScreenResult("X", False))
    out = Screener(prov).scan(["AAPL"])
    assert out == [] and called["n"] == 0


def test_scan_collects_passing_results(monkeypatch):
    index = _trend_bars(start=60, end=108)  # uptrend -> regime ok
    funds = Fundamentals(2e9, 1.1, None, "USD")
    prov = _FakeProvider(_trend_bars(), index, funds)
    plan = size_trade_gbp(100.0, 95.0)
    passing = ScreenResult("AAPL", True, price=100.0, reasons=["r"], risks=["k"], plan=plan)
    monkeypatch.setattr(screen, "evaluate_symbol", lambda *a, **k: passing)
    out = Screener(prov).scan(["AAPL"])
    assert len(out) == 1 and out[0].symbol == "AAPL"


# ----------------------- alerts formatting -----------------------

def _passing_result():
    plan = size_trade_gbp(50.0, 48.5, max_position_pct=10.0)  # 3.33 shares
    return ScreenResult("AAPL", True, price=50.0,
                        reasons=["uptrend intact", "7% pullback"],
                        risks=["earnings in ~20 days"], plan=plan)


def test_format_alert_has_all_eight_fields():
    text = alerts.format_alert(_passing_result())
    for needle in ("AAPL", "Why it matches", "Entry:", "Stop-loss:",
                   "Target (2:1):", "Position:", "Manage (validated plan)",
                   "break-even", "trail 2%", "20 trading days", "Key risks:"):
        assert needle in text


def test_format_digest_empty_and_nonempty():
    assert "no qualifying setups" in alerts.format_digest([])
    d = alerts.format_digest([_passing_result()])
    assert "1 setup(s)" in d and "AAPL" in d


# ----------------------- end-to-end: a real qualifying setup -----------------------

def _qualifying_bars(n=210):
    closes = [60 + 48 * i / (n - 1) for i in range(n)]
    closes[-7], closes[-6], closes[-5], closes[-4], closes[-3] = 107.5, 105, 103, 101.5, 100.5
    opens = list(closes)
    highs = [c + 0.4 for c in closes]
    lows = [c - 0.4 for c in closes]
    vols = [1_200_000] * n
    opens[-2], closes[-2], highs[-2], lows[-2] = 101.0, 100.0, 101.2, 99.5  # prev red
    opens[-1], closes[-1], highs[-1], lows[-1] = 99.7, 103.5, 103.8, 99.5   # green engulfs
    vols[-1] = 1_800_000
    return pd.DataFrame({"open": opens, "high": highs, "low": lows,
                         "close": closes, "volume": vols})


def _flat_index(n=210):
    return pd.DataFrame({"open": [100] * n, "high": [100.2] * n,
                         "low": [99.8] * n, "close": [100] * n, "volume": [1] * n})


def test_evaluate_symbol_full_pass_produces_plan():
    f = Fundamentals(2e9, 1.1, datetime(2030, 1, 1, tzinfo=timezone.utc), "USD")
    r = evaluate_symbol("TEST", _qualifying_bars(), _flat_index(), f)
    assert r.passed and r.failures == []
    assert r.plan is not None
    # 2:1 reward:risk and stop below entry
    risk = r.plan.entry_price - r.plan.stop_price
    assert r.plan.take_profit_price == pytest.approx(r.plan.entry_price + 2 * risk, rel=1e-3)
    assert r.plan.stop_price < r.plan.entry_price < r.plan.take_profit_price


def test_full_pass_flags_fx_for_non_gbp():
    f = Fundamentals(2e9, 1.1, datetime(2030, 1, 1, tzinfo=timezone.utc), "USD")
    r = evaluate_symbol("TEST", _qualifying_bars(), _flat_index(), f)  # USD, fx default
    assert any("FX" in risk for risk in r.risks)


# ----------------------- FX -----------------------

def test_fx_ticker_format():
    from brokebyte.screener import yfinance_provider as yp
    assert yp.fx_ticker("USD") == "GBPUSD=X"
    assert yp.fx_ticker("eur") == "GBPEUR=X"


def test_screener_uses_fx_for_us_sizing(monkeypatch):
    index = _trend_bars(start=60, end=108)

    class _FXProvider(_FakeProvider):
        def fx_per_gbp(self, currency):
            return 1.25  # £1 = $1.25

    funds = Fundamentals(2e9, 1.1, datetime(2030, 1, 1, tzinfo=timezone.utc), "USD")
    prov = _FXProvider(_qualifying_bars(), index, funds)
    captured = {}

    def _capture(sym, bars, idx, f, *, account=500.0, now=None, fx_per_gbp=1.0):
        captured["fx"] = fx_per_gbp
        return ScreenResult(sym, False)

    monkeypatch.setattr(screen, "evaluate_symbol", _capture)
    Screener(prov).scan(["AAPL"])
    assert captured["fx"] == 1.25


def test_fx_applied_in_sizing():
    # £5 risk at £1=$1.25 -> $6.25 budget; entry $100 stop $95 (risk $5/sh)
    # shares_by_risk = floor(6.25/5, 2) = 1.25 ; exposure cap £100*1.25=$125/100=1.25
    p = size_trade_gbp(100.0, 95.0, fx_per_gbp=1.25, max_position_pct=10.0)
    assert p is not None and p.shares == 1.25


def test_funnel_stage_classification():
    from brokebyte.screener.screen import Screener
    f = Screener._funnel_stage
    assert f(["insufficient history (<210 bars)"]) == "data"
    assert f(["price 3.2 outside [5.0, 200.0]"]) == "universe"
    assert f(["earnings date unknown (fail-closed)"]) == "universe"
    assert f(["price not above 50SMA"]) == "trend"
    assert f(["pullback 1.2% outside 3-10%"]) == "setup"
    assert f(["RSI 65.0 outside [40.0, 60.0]"]) == "setup"
    assert f(["does not outperform index"]) == "setup"
    assert f(["RSI did not cross back above 40"]) == "trigger"
    assert f(["no bullish reversal candle"]) == "trigger"


def test_scan_populates_funnel_stats():
    from brokebyte.screener.screen import Screener
    provider = _FakeProvider(_trend_bars(), _trend_bars(), Fundamentals(2e9, 1.1, None, "USD"))
    s = Screener(provider, account=500.0)
    s.scan(["AAPL"])
    stats = s.last_scan_stats
    assert stats["scanned"] == 1
    assert stats["passed"] + sum(stats[k] for k in ("regime_blocked", "fetch_failed", "data", "universe", "trend", "setup", "trigger")) == 1
    assert "funnel:" in Screener.format_scan_stats(stats)
