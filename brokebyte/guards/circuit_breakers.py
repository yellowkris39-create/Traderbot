"""Guard 11 — Circuit Breakers + Alerts.

In-process breaker that halts new entries on anomalies: too many trades in
the trailing hour, or too many consecutive execution errors. (Drawdown
breach is covered separately by risk/portfolio.check_daily_loss_halt.)

Push notifications to a phone (Module 13) plug in behind AlertSink later;
LoggingAlertSink is the Phase 2 default — every trip is logged as a
structured event so nothing is silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from brokebyte.common import CheckResult
from brokebyte.logging_setup import get_logger
from brokebyte.risk.limits import RiskLimits

logger = get_logger(__name__)


class AlertSink:
    """Alert hook. Default logs structured events; Module 13 swaps in a
    push-notification sink behind the same interface."""

    def send(self, level: str, message: str, **fields) -> None:
        raise NotImplementedError


class LoggingAlertSink(AlertSink):
    def send(self, level: str, message: str, **fields) -> None:
        log_fn = getattr(logger, level, logger.info)
        log_fn(message, **fields)


@dataclass
class CircuitBreaker:
    """Tracks recent trade timestamps and consecutive errors in-process."""

    alert_sink: AlertSink = field(default_factory=LoggingAlertSink)
    consecutive_errors: int = 0
    trade_times: list[datetime] = field(default_factory=list)

    def record_trade(self, when: datetime | None = None) -> None:
        self.trade_times.append(when or datetime.now(timezone.utc))

    def record_error(self) -> None:
        self.consecutive_errors += 1

    def record_success(self) -> None:
        self.consecutive_errors = 0

    def check_trade_rate(self, limits: RiskLimits, now: datetime | None = None) -> CheckResult:
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=1)
        recent = [t for t in self.trade_times if t >= cutoff]
        if len(recent) >= limits.max_trades_per_hour:
            self.alert_sink.send(
                "warning",
                "circuit breaker: trade rate limit reached",
                trades_last_hour=len(recent),
                max_trades_per_hour=limits.max_trades_per_hour,
            )
            return CheckResult(False, f"trades in last hour {len(recent)} >= max {limits.max_trades_per_hour}")
        return CheckResult(True)

    def check_consecutive_errors(self, limits: RiskLimits) -> CheckResult:
        if self.consecutive_errors >= limits.max_consecutive_errors:
            self.alert_sink.send(
                "error",
                "circuit breaker: max consecutive errors reached",
                consecutive_errors=self.consecutive_errors,
                max_consecutive_errors=limits.max_consecutive_errors,
            )
            return CheckResult(
                False,
                f"consecutive errors {self.consecutive_errors} >= max {limits.max_consecutive_errors}",
            )
        return CheckResult(True)
