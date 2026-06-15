from datetime import datetime, timedelta, timezone

from brokebyte.guards.circuit_breakers import AlertSink, CircuitBreaker
from brokebyte.risk.limits import RiskLimits

LIMITS = RiskLimits()  # max_trades_per_hour=6, max_consecutive_errors=3


class RecordingAlertSink(AlertSink):
    def __init__(self):
        self.calls = []

    def send(self, level, message, **fields):
        self.calls.append((level, message, fields))


# --- consecutive errors ---------------------------------------------------


def test_consecutive_errors_under_limit_passes():
    breaker = CircuitBreaker(alert_sink=RecordingAlertSink())
    for _ in range(LIMITS.max_consecutive_errors - 1):
        breaker.record_error()

    assert breaker.check_consecutive_errors(LIMITS).ok


def test_consecutive_errors_at_limit_trips_and_alerts():
    sink = RecordingAlertSink()
    breaker = CircuitBreaker(alert_sink=sink)
    for _ in range(LIMITS.max_consecutive_errors):
        breaker.record_error()

    result = breaker.check_consecutive_errors(LIMITS)

    assert not result.ok
    assert "consecutive errors" in result.reason
    assert sink.calls and sink.calls[0][0] == "error"


def test_record_success_resets_consecutive_errors():
    breaker = CircuitBreaker(alert_sink=RecordingAlertSink())
    for _ in range(LIMITS.max_consecutive_errors):
        breaker.record_error()

    breaker.record_success()

    assert breaker.consecutive_errors == 0
    assert breaker.check_consecutive_errors(LIMITS).ok


# --- trade rate ------------------------------------------------------------


def test_trade_rate_under_limit_passes():
    breaker = CircuitBreaker(alert_sink=RecordingAlertSink())
    now = datetime.now(timezone.utc)
    for _ in range(LIMITS.max_trades_per_hour - 1):
        breaker.record_trade(now)

    assert breaker.check_trade_rate(LIMITS, now=now).ok


def test_trade_rate_at_limit_trips_and_alerts():
    sink = RecordingAlertSink()
    breaker = CircuitBreaker(alert_sink=sink)
    now = datetime.now(timezone.utc)
    for _ in range(LIMITS.max_trades_per_hour):
        breaker.record_trade(now)

    result = breaker.check_trade_rate(LIMITS, now=now)

    assert not result.ok
    assert "trades in last hour" in result.reason
    assert sink.calls and sink.calls[0][0] == "warning"


def test_trade_rate_ignores_trades_older_than_an_hour():
    breaker = CircuitBreaker(alert_sink=RecordingAlertSink())
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=2)
    for _ in range(LIMITS.max_trades_per_hour):
        breaker.record_trade(old)

    assert breaker.check_trade_rate(LIMITS, now=now).ok
