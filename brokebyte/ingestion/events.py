"""Normalized event data model for the ingestion stage.

Milestone 1 exercises the pipeline with a single hardcoded NewsEvent.
Live ingestion (Alpaca NewsDataStream, dedup, "already priced in" check)
is wired up in Phase 6 behind the same NewsEvent shape so downstream
stages don't need to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alpaca.data.models.news import News


@dataclass(frozen=True)
class NewsEvent:
    id: str
    headline: str
    summary: str
    symbols: list[str] = field(default_factory=list)
    source: str = "unknown"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def from_alpaca_news(news: "News") -> NewsEvent:
    """Map an Alpaca News websocket message to a normalized NewsEvent."""
    return NewsEvent(
        id=str(news.id),
        headline=news.headline,
        summary=news.summary or "",
        symbols=list(news.symbols) if news.symbols else [],
        source=news.source or "alpaca",
        created_at=news.created_at if news.created_at is not None else datetime.now(timezone.utc),
    )


def hardcoded_signal() -> NewsEvent:
    """A single fixed event used to exercise the pipeline end-to-end (Milestone 1 only)."""
    return NewsEvent(
        id="milestone1-hardcoded-0001",
        headline="Example Corp announces new product line",
        summary=(
            "Placeholder headline used to validate the ingestion -> LLM -> "
            "risk -> execution pipeline before real news ingestion is wired up."
        ),
        symbols=["AAPL"],
        source="hardcoded",
    )
