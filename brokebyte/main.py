"""Phase 3 entry point: ingestion -> LLM (Haiku/Sonnet two-tier) -> risk gate
(sizing, portfolio limits, guards 8-11) -> execution (bracket orders / kill
switch).

Still a single hardcoded signal — live ingestion is wired up in a later
phase behind the same NewsEvent shape, so this wiring doesn't need to change.

Run with:
    venv\\Scripts\\python.exe -m brokebyte.main
"""

from __future__ import annotations

from brokebyte.config import load_config
from brokebyte.execution.broker import Broker
from brokebyte.execution.market_data import MarketData
from brokebyte.guards.circuit_breakers import CircuitBreaker
from brokebyte.ingestion.events import hardcoded_signal
from brokebyte.llm.claude_provider import build_claude_provider
from brokebyte.logging_setup import configure_logging, get_logger
from brokebyte.risk import gate
from brokebyte.risk import portfolio as portfolio_module
from brokebyte.risk.limits import load_risk_limits


def run_once() -> None:
    config = load_config()
    configure_logging(config.log_dir)
    log = get_logger("brokebyte.main")
    limits = load_risk_limits()

    log.info("startup", trading_mode=config.trading_mode, paper=config.is_paper)

    broker = Broker(config)
    market_data = MarketData(config)
    circuit_breaker = CircuitBreaker()

    account = broker.get_account_summary()
    log.info("account_summary", **account)

    portfolio = portfolio_module.from_account_and_positions(account, broker.get_positions())

    event = hardcoded_signal()
    log.info(
        "ingestion_event",
        event_id=event.id,
        headline=event.headline,
        symbols=event.symbols,
        source=event.source,
    )

    if not event.symbols:
        log.info("risk_gate_decision", event_id=event.id, decision="HOLD", reason="event has no symbols")
        return

    provider = build_claude_provider(config)
    verdict = provider.evaluate(event)
    log.info(
        "llm_verdict",
        event_id=event.id,
        material=verdict.material,
        symbol=verdict.symbol,
        direction=verdict.direction.value,
        confidence=verdict.confidence,
        time_horizon=verdict.time_horizon.value,
        is_already_priced_in=verdict.is_already_priced_in,
        reasoning=verdict.reasoning,
    )

    symbol = event.symbols[0]
    bars = market_data.get_daily_bars(symbol)
    quote = market_data.get_quote(symbol)
    log.info("market_data", symbol=symbol, bar_count=len(bars), bid=quote.bid_price, ask=quote.ask_price)

    decision = gate.evaluate(verdict, event, bars, quote, portfolio, limits, circuit_breaker)
    log.info(
        "risk_gate_decision",
        event_id=event.id,
        decision=decision.action,
        reason=decision.reason,
        kill_switch_reason=decision.kill_switch_reason,
    )

    if decision.kill_switch_reason:
        result = broker.kill_switch(decision.kill_switch_reason)
        log.warning(
            "kill_switch_executed",
            reason=result.reason,
            positions_closed=result.positions_closed,
            orders_cancelled=result.orders_cancelled,
        )

    if decision.action != "ENTER" or decision.plan is None:
        return

    plan = decision.plan
    try:
        order = broker.submit_bracket_order(plan)
    except Exception as exc:
        circuit_breaker.record_error()
        log.error("order_submission_failed", event_id=event.id, symbol=plan.symbol, error=str(exc))
        return

    circuit_breaker.record_success()
    circuit_breaker.record_trade()
    log.info(
        "order_submitted",
        event_id=event.id,
        order_id=str(order.id),
        symbol=order.symbol,
        side=str(order.side),
        qty=plan.qty,
        stop_price=plan.stop_price,
        take_profit_price=plan.take_profit_price,
        status=str(order.status),
    )


if __name__ == "__main__":
    run_once()
