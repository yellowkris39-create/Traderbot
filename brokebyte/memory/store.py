"""Module 7 — Trade/Decision Memory Store (storage layer).

SQLite-backed log of every risk-gate decision (ENTER or HOLD) with the full
context available at decision time: the triggering news event, the LLM
verdict, the fused TradeProposal (regime + support/resistance, when the gate
reached Module 3), and the gate's outcome (action, reason, and sizing if
entered).

This is the raw substrate for later phases: Track B's forward-paper
evaluation (§5a metrics computed over this log, see
brokebyte.memory.metrics) and Module 7's retrieval/calibration layers.

ENTER rows additionally carry an *outcome* (exit_price, exit_reason, pnl,
closed_at), recorded after the fact via record_outcome() once a position
closes. closed_trade_pnls() feeds those into
brokebyte.backtest.metrics.compute_metrics for §5a's success metrics.
Detecting that a position closed (polling the broker / a position-monitoring
loop) and "what-if" outcomes for HOLDs are added in a later phase - this
store only provides the schema and accessors for outcomes once known.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from brokebyte.guards.regime import Trend
from brokebyte.ingestion.events import NewsEvent
from brokebyte.llm.provider import LLMVerdict
from brokebyte.risk.gate import GateDecision

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    event_id TEXT NOT NULL,
    headline TEXT NOT NULL,
    symbols TEXT NOT NULL,
    source TEXT NOT NULL,
    verdict_material INTEGER NOT NULL,
    verdict_symbol TEXT,
    verdict_direction TEXT NOT NULL,
    verdict_confidence REAL NOT NULL,
    verdict_time_horizon TEXT NOT NULL,
    verdict_reasoning TEXT NOT NULL,
    verdict_is_already_priced_in INTEGER NOT NULL,
    regime_trend TEXT,
    regime_high_volatility INTEGER,
    regime_size_multiplier REAL,
    support REAL,
    resistance REAL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    kill_switch_reason TEXT,
    plan_side TEXT,
    plan_qty INTEGER,
    plan_entry_price REAL,
    plan_stop_price REAL,
    plan_take_profit_price REAL,
    plan_risk_amount REAL,
    plan_notional REAL,
    broker_order_id TEXT,
    exit_price REAL,
    exit_reason TEXT,
    pnl REAL,
    closed_at TEXT
)
"""

# Columns added after the initial Phase 5a schema. ALTER TABLE ADD COLUMN'd
# onto pre-existing decisions.db files in _migrate so accumulated history
# isn't lost when the schema grows.
_OUTCOME_COLUMNS = {
    "broker_order_id": "TEXT",
    "exit_price": "REAL",
    "exit_reason": "TEXT",
    "pnl": "REAL",
    "closed_at": "TEXT",
}


class DecisionStore:
    """Append-only log of gate decisions, one row per evaluated NewsEvent."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        conn = self._connect()
        try:
            conn.execute(SCHEMA)
            self._migrate(conn)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add any outcome columns missing from a decisions.db created
        before they existed. A no-op for freshly created tables, which
        already have them via SCHEMA."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(decisions)")}
        for name, col_type in _OUTCOME_COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE decisions ADD COLUMN {name} {col_type}")

    def record(self, event: NewsEvent, verdict: LLMVerdict, decision: GateDecision) -> int:
        """Persist one gate decision. Returns the new row's id."""
        proposal = decision.proposal
        plan = decision.plan

        row = (
            datetime.now(timezone.utc).isoformat(),
            event.id,
            event.headline,
            ",".join(event.symbols),
            event.source,
            int(verdict.material),
            verdict.symbol,
            verdict.direction.value,
            verdict.confidence,
            verdict.time_horizon.value,
            verdict.reasoning,
            int(verdict.is_already_priced_in),
            proposal.regime.trend.value if proposal else None,
            int(proposal.regime.high_volatility) if proposal else None,
            proposal.regime.size_multiplier if proposal else None,
            proposal.support if proposal else None,
            proposal.resistance if proposal else None,
            decision.action,
            decision.reason,
            decision.kill_switch_reason,
            plan.side if plan else None,
            plan.qty if plan else None,
            plan.entry_price if plan else None,
            plan.stop_price if plan else None,
            plan.take_profit_price if plan else None,
            plan.risk_amount if plan else None,
            plan.notional if plan else None,
        )

        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO decisions (
                    recorded_at, event_id, headline, symbols, source,
                    verdict_material, verdict_symbol, verdict_direction, verdict_confidence,
                    verdict_time_horizon, verdict_reasoning, verdict_is_already_priced_in,
                    regime_trend, regime_high_volatility, regime_size_multiplier, support, resistance,
                    action, reason, kill_switch_reason,
                    plan_side, plan_qty, plan_entry_price, plan_stop_price, plan_take_profit_price,
                    plan_risk_amount, plan_notional
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            conn.commit()
            return int(cursor.lastrowid)
        finally:
            conn.close()

    def recent(self, limit: int = 50) -> list[sqlite3.Row]:
        """Most recently recorded decisions first."""
        conn = self._connect()
        try:
            cursor = conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))
            return cursor.fetchall()
        finally:
            conn.close()

    def count(self) -> int:
        conn = self._connect()
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM decisions")
            return int(cursor.fetchone()[0])
        finally:
            conn.close()

    def record_outcome(
        self,
        decision_id: int,
        exit_price: float,
        exit_reason: str,
        pnl: float,
        closed_at: datetime | None = None,
    ) -> None:
        """Attach a realized outcome to a previously recorded ENTER decision,
        once a future position-monitoring loop detects the position closed."""
        closed_at = closed_at or datetime.now(timezone.utc)

        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE decisions SET exit_price = ?, exit_reason = ?, pnl = ?, closed_at = ? WHERE id = ?",
                (exit_price, exit_reason, pnl, closed_at.isoformat(), decision_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise ValueError(f"no decision with id={decision_id}")
        finally:
            conn.close()

    def closed_trade_pnls(self) -> list[float]:
        """pnl for every decision with a recorded outcome, oldest first --
        feeds directly into brokebyte.backtest.metrics.compute_metrics for
        §5a's success metrics over Track B's forward-paper soak."""
        conn = self._connect()
        try:
            cursor = conn.execute("SELECT pnl FROM decisions WHERE pnl IS NOT NULL ORDER BY id")
            return [row["pnl"] for row in cursor.fetchall()]
        finally:
            conn.close()

    def regime_coverage(self) -> dict[Trend, int]:
        """Tally how often each Trend was in effect across every decision
        that reached Module 3 (ENTER or HOLD), for §5a "regime coverage"
        reporting over Track B's soak."""
        counts = {Trend.UP: 0, Trend.DOWN: 0, Trend.CHOPPY: 0}
        conn = self._connect()
        try:
            cursor = conn.execute("SELECT regime_trend FROM decisions WHERE regime_trend IS NOT NULL")
            for row in cursor.fetchall():
                counts[Trend(row["regime_trend"])] += 1
            return counts
        finally:
            conn.close()

    def query_similar(self, regime_trend: str, k: int = 5) -> list[sqlite3.Row]:
        """Return up to k closed ENTER decisions matching `regime_trend`,
        newest first. Raw DB query used by brokebyte.memory.retrieval."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT * FROM decisions "
                "WHERE action='ENTER' AND pnl IS NOT NULL AND regime_trend=? "
                "ORDER BY id DESC LIMIT ?",
                (regime_trend, k),
            )
            return cursor.fetchall()
        finally:
            conn.close()

    def closed_enter_decisions(self) -> list[sqlite3.Row]:
        """All closed ENTER decisions with recorded outcomes, oldest first.
        Used by brokebyte.memory.calibration."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT * FROM decisions WHERE action='ENTER' AND pnl IS NOT NULL ORDER BY id"
            )
            return cursor.fetchall()
        finally:
            conn.close()

    def open_enter_decisions(self) -> list[sqlite3.Row]:
        """All ENTER decisions with no recorded outcome yet (position still open).
        Used by brokebyte.monitor.reconcile to find positions to reconcile."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT * FROM decisions WHERE action='ENTER' AND pnl IS NULL ORDER BY id"
            )
            return cursor.fetchall()
        finally:
            conn.close()

    def update_order_id(self, decision_id: int, broker_order_id: str) -> None:
        """Record the Alpaca order ID for an ENTER decision after the bracket
        order is successfully submitted. Used by main.py and the monitor."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE decisions SET broker_order_id = ? WHERE id = ?",
                (broker_order_id, decision_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise ValueError(f"no decision with id={decision_id}")
        finally:
            conn.close()
