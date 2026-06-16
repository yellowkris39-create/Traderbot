"""Module 7 — Retrieval layer: fetch similar past setups from DecisionStore
at decision time, to inform the LLM verdict (SPEC.md Module 7 / Phase 5d).

Approach: structured (SQL) similarity rather than NLP-embedding-based.
The 'similarity' that matters for a trading setup is structural -- same
market regime, same directional context -- not semantic proximity of headline
text.  The LLM has already distilled each news event's meaning into the
structured verdict fields (direction, confidence); regime + those fields
identify "similar conditions" more directly than cosine distance over raw
headline embeddings would.  A future upgrade to dense embeddings (e.g.
sentence-transformers) is possible if the soak data shows structural matching
is insufficient, but this approach adds zero new dependencies and is
interpretable.

retrieve_similar() is guarded by MIN_RETRIEVAL_SAMPLE: if the store has fewer
closed ENTER decisions than that threshold, it returns [] so the LLM prompt
is unchanged (no stale or vacuous historical context injected).

format_similar_setups() converts the returned rows into a string for injection
into the LLM prompt via build_user_prompt(historical_context=...).  Only
structured bot-generated fields (dates, regime, direction, confidence, outcome)
are included -- raw headline text from past events is intentionally excluded
to avoid re-injecting potentially crafted input from an old news item.
"""

from __future__ import annotations

import sqlite3

from brokebyte.memory.store import DecisionStore

MIN_RETRIEVAL_SAMPLE = 5  # minimum closed ENTER decisions before retrieval activates


def retrieve_similar(
    store: DecisionStore,
    regime_trend: str,
    k: int = 5,
) -> list[sqlite3.Row]:
    """Return up to `k` closed ENTER decisions from `store` that share
    `regime_trend`, newest first.

    Returns [] if the store has fewer than MIN_RETRIEVAL_SAMPLE total closed
    trades, ensuring the LLM prompt is unchanged until there is meaningful
    history to draw on."""
    if len(store.closed_trade_pnls()) < MIN_RETRIEVAL_SAMPLE:
        return []
    return store.query_similar(regime_trend, k)


def format_similar_setups(rows: list[sqlite3.Row]) -> str:
    """Format retrieved past decisions into a plain-text block for injection
    into the LLM verdict prompt via build_user_prompt(historical_context=...).

    Only structured bot-generated fields are included (not raw headline text).
    Returns "" when rows is empty."""
    if not rows:
        return ""
    lines = [f"Past setups in the same market regime ({len(rows)} most recent closed trades):"]
    for i, row in enumerate(rows, 1):
        pnl_str = f"${row['pnl']:+.2f}" if row["pnl"] is not None else "open"
        if row["exit_reason"] and row["pnl"] is not None:
            outcome = f"{row['exit_reason']} -> {pnl_str}"
        else:
            outcome = "no outcome recorded"
        lines.append(
            f"  {i}. [{row['recorded_at'][:10]}] direction={row['verdict_direction']}"
            f" regime={row['regime_trend']} confidence={row['verdict_confidence']:.2f}"
            f" | {outcome}"
        )
    return "\n".join(lines)
