"""Module 7 — Trade/Decision Memory Store (storage layer).

SQLite-backed log of every risk-gate decision (ENTER or HOLD) with the full
context available at decision time: the triggering news event, the LLM
verdict, the fused TradeProposal (regime + support/resistance, when the gate
reached Module 3), and the gate's outcome (action, reason, and sizing if
entered).

This is the raw substrate for later phases: Track B's forward-paper
evaluation (§5a metrics computed over this log) and Module 7's
retrieval/calibration layers. Entry/exit/P&L reconciliation for filled
trades, and "what-if" outcomes for HOLDs, are added in a later phase once a
position-monitoring loop exists - this store only records what the gate knew
at decision time.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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
    plan_notional REAL
)
"""


class DecisionStore:
    """Append-only log of gate decisions, one row per evaluated NewsEvent."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        conn = self._connect()
        try:
            conn.execute(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

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
