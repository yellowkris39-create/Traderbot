import pytest

from brokebyte.risk import portfolio
from brokebyte.risk.limits import RiskLimits
from brokebyte.risk.sizing import PositionPlan

LIMITS = RiskLimits()  # max_open_positions=5, max_daily_loss_pct=0.02, max_position_pct=0.10


def make_portfolio(equity=100_000, last_equity=100_000, positions=None):
    return portfolio.PortfolioState(equity=equity, cash=equity, last_equity=last_equity, positions=positions or {})


def make_plan(symbol="AAPL", qty=10, entry_price=100.0):
    return PositionPlan(
        symbol=symbol,
        side="buy",
        qty=qty,
        entry_price=entry_price,
        stop_price=entry_price * 0.96,
        take_profit_price=entry_price * 1.08,
        risk_amount=qty * entry_price * 0.04,
        notional=qty * entry_price,
    )


def test_from_account_and_positions():
    account = {"equity": "100000", "cash": "90000", "last_equity": "101000"}
    positions = [{"symbol": "AAPL", "qty": "10", "market_value": "1000"}]

    state = portfolio.from_account_and_positions(account, positions)

    assert state.equity == 100_000
    assert state.last_equity == 101_000
    assert state.daily_pnl == pytest.approx(-1000)
    assert state.positions["AAPL"].qty == 10


# --- daily loss halt ---------------------------------------------------


def test_daily_loss_within_limit_allows():
    state = make_portfolio(equity=99_000, last_equity=100_000)  # -1% loss

    result = portfolio.check_daily_loss_halt(state, LIMITS)

    assert result.ok


def test_daily_loss_at_limit_halts():
    state = make_portfolio(equity=98_000, last_equity=100_000)  # -2% loss, limit is 2%

    result = portfolio.check_daily_loss_halt(state, LIMITS)

    assert not result.ok
    assert "daily loss" in result.reason


def test_daily_gain_allows():
    state = make_portfolio(equity=105_000, last_equity=100_000)

    assert portfolio.check_daily_loss_halt(state, LIMITS).ok


# --- max open positions -------------------------------------------------


def test_max_open_positions_allows_under_limit():
    state = make_portfolio(positions={f"SYM{i}": portfolio.PositionInfo(f"SYM{i}", 1, 100) for i in range(3)})

    assert portfolio.check_max_open_positions(state, "NEWSYM", LIMITS).ok


def test_max_open_positions_rejects_new_name_at_limit():
    state = make_portfolio(
        positions={f"SYM{i}": portfolio.PositionInfo(f"SYM{i}", 1, 100) for i in range(LIMITS.max_open_positions)}
    )

    result = portfolio.check_max_open_positions(state, "NEWSYM", LIMITS)

    assert not result.ok


def test_max_open_positions_allows_adding_to_existing_at_limit():
    positions = {f"SYM{i}": portfolio.PositionInfo(f"SYM{i}", 1, 100) for i in range(LIMITS.max_open_positions)}
    state = make_portfolio(positions=positions)

    # SYM0 is already held, so adding more of it doesn't increase the count.
    assert portfolio.check_max_open_positions(state, "SYM0", LIMITS).ok


# --- exposure ------------------------------------------------------------


def test_exposure_within_limit_allows():
    state = make_portfolio(equity=100_000)
    plan = make_plan(qty=50, entry_price=100.0)  # notional 5,000 < 10% of 100k

    assert portfolio.check_exposure(state, plan, LIMITS).ok


def test_exposure_over_limit_rejects():
    state = make_portfolio(equity=100_000)
    plan = make_plan(qty=200, entry_price=100.0)  # notional 20,000 > 10% of 100k

    result = portfolio.check_exposure(state, plan, LIMITS)

    assert not result.ok


def test_exposure_accounts_for_existing_position():
    state = make_portfolio(
        equity=100_000,
        positions={"AAPL": portfolio.PositionInfo("AAPL", 80, 8_000)},  # already 8% exposed
    )
    plan = make_plan(symbol="AAPL", qty=30, entry_price=100.0)  # +3,000 -> total 11,000 > 10,000 cap

    result = portfolio.check_exposure(state, plan, LIMITS)

    assert not result.ok
