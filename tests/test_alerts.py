"""Tests for screener alert formatting and digest assembly."""

from __future__ import annotations

from brokebyte.screener.alerts import format_alert, format_digest
from brokebyte.screener.screen import ScreenResult
from brokebyte.screener.sizing_gbp import GbpTradePlan


def _plan(entry=100.0, stop=95.0, target=110.0):
    risk = entry - stop
    return GbpTradePlan(
        shares=10,
        entry_price=entry,
        stop_price=stop,
        take_profit_price=target,
        risk_per_share=risk,
        risk_amount=10 * risk,
        notional=10 * entry,
        exposure_capped=False,
    )


def _result(symbol="AAPL", passed=True, capped=False):
    plan = _plan()
    if capped:
        plan = GbpTradePlan(
            shares=5, entry_price=100.0, stop_price=95.0,
            take_profit_price=110.0, risk_per_share=5.0,
            risk_amount=25.0, notional=500.0, exposure_capped=True,
        )
    return ScreenResult(
        symbol=symbol, passed=passed, price=100.0,
        reasons=["RSI crossed up from oversold", "above 200SMA"],
        risks=["earnings in 8 days", "thin volume"],
        plan=plan,
    )


def test_format_alert_contains_required_fields():
    out = format_alert(_result())
    assert "AAPL" in out
    assert "100.00" in out       # entry price
    assert "95.00" in out        # stop
    assert "110.00" in out       # target
    assert "break-even" in out
    assert "20 trading days" in out
    assert "RSI crossed" in out
    assert "earnings" in out


def test_format_alert_indexed():
    out = format_alert(_result(), index=3)
    assert out.startswith("3. AAPL")


def test_format_alert_exposure_capped_flag():
    out = format_alert(_result(capped=True))
    assert "exposure-capped" in out


def test_format_alert_no_cap_no_flag():
    out = format_alert(_result(capped=False))
    assert "exposure-capped" not in out


def test_format_digest_single():
    out = format_digest([_result("TSLA")])
    assert "1 setup" in out
    assert "TSLA" in out
    assert "not advice" in out


def test_format_digest_multi():
    out = format_digest([_result("AAPL"), _result("MSFT")])
    assert "2 setup" in out
    assert "1. AAPL" in out
    assert "2. MSFT" in out


def test_format_digest_empty():
    out = format_digest([])
    assert "no qualifying" in out


def test_format_digest_custom_header():
    out = format_digest([], header="MyBot")
    assert "MyBot" in out
