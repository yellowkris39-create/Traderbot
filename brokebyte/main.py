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

import os
import re
import signal
import sys
from datetime import datetime, timezone

import pandas as pd

from brokebyte.common import Quote
from brokebyte.config import Config, load_config
from brokebyte.execution.broker import Broker
from brokebyte.execution.market_data import MarketData
from brokebyte.guards.circuit_breakers import CircuitBreaker
from brokebyte.guards.regime import classify_regime
from brokebyte.ingestion.events import NewsEvent, hardcoded_signal
from brokebyte.llm.claude_provider import build_claude_provider
from brokebyte.llm.provider import Direction, LLMProvider
from brokebyte.logging_setup import configure_logging, get_logger
from brokebyte.memory.retrieval import format_similar_setups, retrieve_similar
from brokebyte.memory.store import DecisionStore
from brokebyte.monitor.exit_manager import manage_open_positions
from brokebyte.monitor.reconcile import reconcile_open_positions
from brokebyte.risk import gate
from brokebyte.risk import portfolio as portfolio_module
from brokebyte.risk.limits import RiskLimits, load_risk_limits
from brokebyte.risk.portfolio import PortfolioState

# ---------------------------------------------------------------------------
# US ticker validation — rejects exchange-prefixed symbols (e.g. TSX:ADW,
# LSE:BARC) that Alpaca's news stream occasionally tags but whose market data
# the US-only Alpaca data API cannot serve.
# ---------------------------------------------------------------------------

_US_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")


def _is_us_ticker(symbol: str) -> bool:
    """Return True only for plain US ticker symbols (1–5 capital letters, no colon)."""
    return bool(_US_TICKER_RE.match(symbol))


# ---------------------------------------------------------------------------
# Portfolio cache — avoids hammering the Alpaca account/positions API on every
# news event.  TTL is configurable via PORTFOLIO_CACHE_SECONDS env var.
# ---------------------------------------------------------------------------

class _PortfolioCache:
    """Caches the PortfolioState for `ttl` seconds to reduce broker API calls."""

    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._state: PortfolioState | None = None
        self._fetched_at: datetime | None = None

    def get(self, broker: Broker) -> PortfolioState:
        now = datetime.now(timezone.utc)
        if (
            self._state is None
            or self._fetched_at is None
            or (now - self._fetched_at).total_seconds() > self._ttl
        ):
            account = broker.get_account_summary()
            self._state = portfolio_module.from_account_and_positions(
                account, broker.get_positions()
            )
            self._fetched_at = now
        return self._state

    def invalidate(self) -> None:
        """Force a fresh fetch on the next call (e.g. after kill switch fires)."""
        self._state = None


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

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
    portfolio_cache = _PortfolioCache(ttl=config.portfolio_cache_seconds)

    # Symbols with an active bracket order this session.  Prevents a second
    # order being placed for the same symbol before the portfolio cache has
    # had time to refresh and reflect the first fill.  Entries are removed
    # when the reconciler records a closed outcome for that symbol.
    active_symbols: set[str] = set()

    account = broker.get_account_summary()
    log.info("account_summary", **account)

    return log, limits, broker, market_data, provider, circuit_breaker, memory, config, portfolio_cache, active_symbols


# ---------------------------------------------------------------------------
# Event processing
# ---------------------------------------------------------------------------

def _process_event(
    event: NewsEvent,
    broker: Broker,
    market_data: MarketData,
    provider: LLMProvider,
    circuit_breaker: CircuitBreaker,
    memory: DecisionStore,
    limits: RiskLimits,
    log,
    portfolio_cache: _PortfolioCache,
    active_symbols: set[str],
) -> None:
    """Run one news event through the full pipeline.

    Pre-LLM: fetches regime/retrieval context from the first tagged symbol
    (informational only — used to hydrate historical context for the LLM).

    Post-LLM: fetches bars and quote for **verdict.symbol** (which may differ
    from event.symbols[0]) so the gate's ATR, regime, liquidity, and sizing
    checks always use the correct instrument's market data.
    """
    # NEWS_PIPELINE_ENABLED=false skips ALL per-event work — no Haiku/Sonnet
    # API calls, no gate, no DB row — while the stream, exit manager,
    # reconciler, and health reporting keep running (none of them use the
    # LLM). This is the "£0/day" switch: with news ENTRIES already paused,
    # the verdicts were pure cost. Flip to true to resume news analysis.
    if os.environ.get("NEWS_PIPELINE_ENABLED", "true").strip().lower() not in ("1", "true", "yes"):
        log.info("news_pipeline_disabled_skip", event_id=event.id)
        return

    portfolio = portfolio_cache.get(broker)

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

    # --- Pre-LLM: regime classification on first tagged symbol for retrieval ---
    # This is informational only; market data for the actual trade is fetched
    # after the LLM identifies verdict.symbol below.
    pre_bars = market_data.get_daily_bars(event.symbols[0])
    pre_regime = classify_regime(pre_bars)
    similar_rows = retrieve_similar(memory, pre_regime.trend.value, k=5)
    historical_context = format_similar_setups(similar_rows)
    if historical_context:
        log.info("retrieval_context", regime=pre_regime.trend.value, similar_count=len(similar_rows))

    # --- LLM evaluation ---
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

    # --- Post-LLM: fetch market data for the verdict symbol ---
    # Only worth fetching when the verdict might actually reach the gate's
    # liquidity/sizing steps.  A dummy Quote / empty DataFrame is safe for
    # early-exit cases (gate fails closed on zero price / missing bars).

    # Reject non-US exchange symbols (e.g. TSX:ADW) — Alpaca's news stream
    # occasionally tags foreign tickers; the US-only data API cannot serve them.
    if verdict.symbol is not None and not _is_us_ticker(verdict.symbol):
        log.info(
            "risk_gate_decision",
            event_id=event.id,
            decision="HOLD",
            reason=f"non-US symbol rejected: {verdict.symbol}",
        )
        return

    if (
        verdict.material
        and verdict.symbol is not None
        and verdict.direction != Direction.NONE
        and not verdict.is_already_priced_in
    ):
        symbol = verdict.symbol
        bars = market_data.get_daily_bars(symbol)
        quote = market_data.get_quote(symbol)
        log.info(
            "market_data",
            symbol=symbol,
            bar_count=len(bars),
            bid=quote.bid_price,
            ask=quote.ask_price,
        )
    else:
        bars = pd.DataFrame()
        quote = Quote(bid_price=0.0, ask_price=0.0)

    # --- Risk gate ---
    decision = gate.evaluate(verdict, event, bars, quote, portfolio, limits, circuit_breaker)

    # --- Pre-persistence execution gates ---
    # The duplicate-order and market-hours checks can still convert an approved
    # ENTER into a HOLD.  Apply them *before* persisting so the decision store
    # never records an ENTER that never reaches the broker: such a row has no
    # broker position and no exit fill, so the reconciler would treat it as a
    # forever-open "phantom" position it can never close.
    if decision.action == "ENTER" and decision.plan is not None:
        if os.environ.get("NEWS_ENTRIES_ENABLED", "false").strip().lower() not in ("1", "true", "yes"):
            # Kris 2026-07-04: news strategy entries PAUSED (never validated);
            # existing positions still wind down via exit_manager/reconciler.
            # The validated swing screener trades via brokebyte.screener.executor.
            decision = gate.GateDecision(
                plan=None,
                reason="news entries paused (NEWS_ENTRIES_ENABLED != true)",
                proposal=decision.proposal,
            )
        elif decision.plan.symbol in active_symbols:
            # The portfolio cache has a TTL, so two events for the same symbol
            # can both pass the gate before the first fill appears.  Block the
            # re-entry until the reconciler confirms the position has closed.
            decision = gate.GateDecision(
                plan=None,
                reason=(
                    f"duplicate order blocked: {decision.plan.symbol} "
                    "already has an active order this session"
                ),
                proposal=decision.proposal,
            )
        elif not broker.is_market_open():
            # Bracket orders submitted outside regular hours may queue at stale
            # prices.  Defer; the news will be re-evaluated during market hours.
            decision = gate.GateDecision(
                plan=None,
                reason="market is closed — entry deferred",
                proposal=decision.proposal,
            )

    log.info(
        "risk_gate_decision",
        event_id=event.id,
        decision=decision.action,
        reason=decision.reason,
        kill_switch_reason=decision.kill_switch_reason,
    )
    decision_id = memory.record(event, verdict, decision)

    # --- Kill switch (daily-loss halt) ---
    if decision.kill_switch_reason:
        result = broker.kill_switch(decision.kill_switch_reason)
        portfolio_cache.invalidate()
        log.warning(
            "kill_switch_executed",
            reason=result.reason,
            positions_closed=result.positions_closed,
            orders_cancelled=result.orders_cancelled,
        )

    if decision.action != "ENTER" or decision.plan is None:
        return

    # --- Order submission ---
    plan = decision.plan
    try:
        order = broker.submit_bracket_order(plan)
    except Exception as exc:
        circuit_breaker.record_error()
        log.error("order_submission_failed", event_id=event.id, symbol=plan.symbol, error=str(exc))
        # The decision was persisted as ENTER above.  Downgrade it so the
        # reconciler does not treat an un-submitted order as an open position.
        memory.mark_not_executed(decision_id, f"order submission failed: {exc}")
        return

    circuit_breaker.record_success()
    circuit_breaker.record_trade()
    portfolio_cache.invalidate()  # positions changed; force fresh fetch next event
    active_symbols.add(plan.symbol)  # block duplicate orders until position closes
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


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------

def run_once() -> None:
    """One-shot mode: process the hardcoded placeholder signal and exit.

    Used for manual smoke-tests and development; invoked with --once.
    """
    log, limits, broker, market_data, provider, circuit_breaker, memory, _config, portfolio_cache, active_symbols = _setup()
    event = hardcoded_signal()
    _process_event(event, broker, market_data, provider, circuit_breaker, memory, limits, log, portfolio_cache, active_symbols)


def run_stream() -> None:
    """Stream mode: process live Alpaca news events continuously.
    Subscribes to Alpaca's NewsDataStream (all symbols), deduplicates events,
    runs each through the full pipeline, and reconciles open positions every
    `config.reconcile_interval_seconds`.  Monitors the websocket thread for
    unexpected death and attempts a single restart before giving up.
    Handles both KeyboardInterrupt (Ctrl-C) and SIGTERM (systemd / Docker)
    for a graceful shutdown that closes the websocket cleanly.
    """
    from brokebyte.ingestion.stream import NewsStream
    log, limits, broker, market_data, provider, circuit_breaker, memory, config, portfolio_cache, active_symbols = _setup()
    stream = NewsStream(config)
    thread = stream.start()
    log.info(
        "news_stream_started",
        reconcile_interval_s=config.reconcile_interval_seconds,
        portfolio_cache_ttl_s=config.portfolio_cache_seconds,
    )
    # --- SIGTERM handler (graceful shutdown for Docker / systemd) ---
    def _handle_sigterm(signum, frame):  # noqa: ANN001
        log.info("stream_stopped", reason="sigterm")
        stream.stop()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _handle_sigterm)
    last_reconcile = datetime.now(timezone.utc)
    try:
        while True:
            # --- Websocket thread health check ---
            if not thread.is_alive():
                log.warning("news_stream_thread_died_attempting_restart")
                try:
                    stream = NewsStream(config)
                    thread = stream.start()
                    log.info("news_stream_restarted")
                except Exception as exc:  # noqa: BLE001
                    log.error("news_stream_restart_failed", error=str(exc))
                    break
            # --- Periodic position reconciliation ---
            now = datetime.now(timezone.utc)
            if (now - last_reconcile).total_seconds() >= config.reconcile_interval_seconds:
                # Active exit management first: move stops to break-even at +1R
                # and force-close positions past the 10-day time-stop. This is
                # what prevents positions drifting open forever (the historical
                # "0 closed trades" bug).
                try:
                    exit_actions = manage_open_positions(broker, memory, log, now=now)
                    if exit_actions:
                        portfolio_cache.invalidate()
                        for act in exit_actions:
                            if act.kind == "close_time_stop":
                                active_symbols.discard(act.symbol)
                except Exception as exc:  # noqa: BLE001 - never let exits kill the loop
                    log.error("exit_manage_error", error=str(exc))

                outcomes = reconcile_open_positions(broker, memory, log)
                # Release the duplicate-order guard for any symbol whose
                # position just closed so the bot can re-enter on fresh news.
                for outcome in outcomes:
                    active_symbols.discard(outcome.symbol)
                last_reconcile = now
            # --- Process next event (None on timeout) ---
            event = stream.get(timeout=60.0)
            if event is not None:
                try:
                    _process_event(
                        event, broker, market_data, provider, circuit_breaker, memory, limits, log, portfolio_cache, active_symbols
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
