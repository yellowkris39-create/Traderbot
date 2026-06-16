"""Entry point: ingestion -> LLM (Haiku/Sonnet two-tier) -> context fusion ->
risk gate (sizing, portfolio limits, guards 8-11) -> execution (bracket
orders / kill switch) -> decision memory (Module 7 storage layer).

Two run modes
-------------
Stream mode (default; Phase 6):
    venv\\Scripts\\python.exe -m brokebyte.main

    Connects to Alpaca's live NewsDataStream websocket, deduplicates events,
    and processes each one through the full pipeline.  Runs the position
    reconciliation monitor every RECONCILE_INTERVAL_SECONDS.

One-shot mode (testing / manual):
    venv\\Scripts\\python.exe -m brokebyte.main --once

    Processes the hardcoded placeholder signal once and exits.  Used during
    development and for smoke-tests.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from brokebyte.config import load_config
from brokebyte.execution.broker import Broker
from brokebyte.execution.market_data import MarketData
from brokebyte.guards.circuit_breakers import CircuitBreaker
from brokebyte.guards.regime import classify_regime
from brokebyte.ingestion.events import NewsEvent, hardcoded_signal
from brokebyte.llm.claude_provider import build_claude_provider
from brokebyte.llm.provider import LLMProvider
from brokebyte.logging_setup import configure_logging, get_logger
from brokebyte.memory.retrieval import format_similar_setups, retrieve_similar
from brokebyte.memory.store import DecisionStore
from brokebyte.monitor.reconcile import reconcile_open_positions
from brokebyte.risk import gate
from brokebyte.risk import portfolio as portfolio_module
from brokebyte.risk.limits import RiskLimits, load_risk_limits

RECONCILE_INTERVAL_SECONDS = 300  # 5 minutes between position-reconciliation runs


def _setup():
    """Create and return all long-lived pipeline resources.

    Called once at startup by both run_once() and run_stream().
    Returns a tuple to keep the function simple; callers unpack by position.
    """
    config = load_config()
    configure_logging(config.log_dir)
    log = get_logger("brokebyte.main")
    limits = load_risk_limits()

    log.info("startup", trading_mode=config.trading_mode, paper=config.is_paper)

    broker = Broker(config)
    market_data = MarketData(config)
    provider = build_claude_provider(config)
    circuit_breaker = CircuitBreaker()
    memory = DecisionStore(config.log_dir / "decisions.db")

    account = broker.get_account_summary()
    log.info("account_summary", **account)

    return log, limits, broker, market_data, provider, circuit_breaker, memory, config


def _process_event(
    event: NewsEvent,
    broker: Broker,
    market_data: MarketData,
    provider: LLMProvider,
    circuit_breaker: CircuitBreaker,
    memory: DecisionStore,
    limits: RiskLimits,
    log,
) -> None:
    """Run one news event through the full pipeline.

    Fetches a fresh portfolio snapshot, classifies regime, runs retrieval,
    calls the LLM, evaluates risk, records the decision, and submits any
    approved bracket order.
    """
    account = broker.get_account_summary()
    portfolio = portfolio_module.from_account_and_positions(account, broker.get_positions())

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

    symbol = event.symbols[0]
    bars = market_data.get_daily_bars(symbol)
    quote = market_data.get_quote(symbol)
    log.info("market_data", symbol=symbol, bar_count=len(bars), bid=quote.bid_price, ask=quote.ask_price)

    regime = classify_regime(bars)
    similar_rows = retrieve_similar(memory, regime.trend.value, k=5)
    historical_context = format_similar_setups(similar_rows)
    if historical_context:
        log.info("retrieval_context", regime=regime.trend.value, similar_count=len(similar_rows))

    verdict = provider.evaluate(event, historical_context=historical_context)
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

    decision = gate.evaluate(verdict, event, bars, quote, portfolio, limits, circuit_breaker)
    log.info(
        "risk_gate_decision",
        event_id=event.id,
        decision=decision.action,
        reason=decision.reason,
        kill_switch_reason=decision.kill_switch_reason,
    )
    decision_id = memory.record(event, verdict, decision)

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
    memory.update_order_id(decision_id, str(order.id))
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


def run_once() -> None:
    """One-shot mode: process the hardcoded placeholder signal and exit.

    Used for manual smoke-tests and development; invoked with --once.
    """
    log, limits, broker, market_data, provider, circuit_breaker, memory, _config = _setup()
    event = hardcoded_signal()
    _process_event(event, broker, market_data, provider, circuit_breaker, memory, limits, log)


def run_stream() -> None:
    """Stream mode: process live Alpaca news events continuously.

    Subscribes to Alpaca's NewsDataStream (all symbols), deduplicates events,
    runs each through the full pipeline, and reconciles open positions every
    RECONCILE_INTERVAL_SECONDS.  Runs until KeyboardInterrupt (Ctrl-C).
    """
    from brokebyte.ingestion.stream import NewsStream

    log, limits, broker, market_data, provider, circuit_breaker, memory, config = _setup()

    stream = NewsStream(config)
    stream.start()
    log.info("news_stream_started", reconcile_interval_s=RECONCILE_INTERVAL_SECONDS)

    last_reconcile = datetime.now(timezone.utc)

    try:
        while True:
            event = stream.get(timeout=60.0)

            now = datetime.now(timezone.utc)
            if (now - last_reconcile).total_seconds() >= RECONCILE_INTERVAL_SECONDS:
                reconcile_open_positions(broker, memory, log)
                last_reconcile = now

            if event is not None:
                try:
                    _process_event(
                        event, broker, market_data, provider, circuit_breaker, memory, limits, log
                    )
                except Exception as exc:  # noqa: BLE001 - never let one bad event kill the loop
                    log.error("event_processing_error", event_id=event.id, error=str(exc))

    except KeyboardInterrupt:
        log.info("stream_stopped", reason="keyboard_interrupt")
        stream.stop()


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once()
    else:
        run_stream()
