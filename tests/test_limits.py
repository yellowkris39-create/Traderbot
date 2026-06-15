from brokebyte.risk.limits import RiskLimits, load_risk_limits


def test_defaults_when_env_unset(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("RISK_"):
            monkeypatch.delenv(key, raising=False)

    limits = load_risk_limits()

    assert limits == RiskLimits()


def test_env_overrides_are_applied(monkeypatch):
    monkeypatch.setenv("RISK_MAX_RISK_PER_TRADE_PCT", "0.01")
    monkeypatch.setenv("RISK_MAX_OPEN_POSITIONS", "3")

    limits = load_risk_limits()

    assert limits.max_risk_per_trade_pct == 0.01
    assert limits.max_open_positions == 3
    # Untouched fields keep their defaults.
    assert limits.max_position_pct == RiskLimits().max_position_pct


def test_risk_limits_is_frozen():
    limits = RiskLimits()
    try:
        limits.max_open_positions = 99  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("RiskLimits must be immutable")
