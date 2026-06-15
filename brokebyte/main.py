"""Milestone 1 entry point: ingestion -> LLM (stub) -> risk gate -> execution.

Proves the pipeline runs end-to-end on paper with a hardcoded signal and a
fixed LLM verdict. No real strategy, news ingestion, or risk module yet —
those land in later phases.

Run with:
    venv\\Scripts\\python.exe -m brokebyte.main
"""

from __future__ import annotations

from brokebyte.config import load_config
from brokebyte.execution.broker import Broker
from brokebyte.ingestion.events import hardcoded_signal
from brokebyte.llm.provider import Direction, LLMVerdict, StubLLMProvider, TimeHorizon
from brokebyte.logging_setup import configure_logging, get_logger
from brokebyte.risk import gate


def build_stub_provider() -> StubLLMProvider:
    """Fixed bullish verdict for Milestone 1 — proves the pipeline runs, nothing more."""
    verdict = LLMVerdict(
        material=True,
        symbol="AAPL",
        direction=Direction.LONG,
        confidence=0.75,
        time_horizon=TimeHorizon.SWING,
        reasoning="Milestone 1 stub verdict — not derived from real news.",
        is_already_priced_in=False,
    )
    return StubLLMProvider(verdict)


def run_once() -> None:
    config = load_config()
    configure_logging(config.log_dir)
    log = get_logger("brokebyte.main")

    log.info("startup", trading_mode=config.trading_mode, paper=config.is_paper)

    broker = Broker(config)
    account = broker.get_account_summary()
    log.info("account_summary", **account)

    event = hardcoded_signal()
    log.info(
        "ingestion_event",
        event_id=event.id,
        headline=event.headline,
        symbols=event.symbols,
        source=event.source,
    )

    provider = build_stub_provider()
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

    intent = gate.evaluate(verdict)
    if intent is None:
        log.info("risk_gate_decision", event_id=event.id, decision="HOLD")
        return

    log.info(
        "risk_gate_decision",
        event_id=event.id,
        decision="PROCEED",
        symbol=intent.symbol,
        side=intent.side,
        qty=intent.qty,
    )

    order = broker.submit_market_order(intent)
    log.info(
        "order_submitted",
        event_id=event.id,
        order_id=str(order.id),
        symbol=order.symbol,
        side=str(order.side),
        qty=order.qty,
        status=str(order.status),
    )


if __name__ == "__main__":
    run_once()
