"""Event deduplication for the live news stream (Phase 6).

Prevents the same news article from being processed more than once within a
rolling time window.  Alpaca assigns a unique integer ID per News article;
deduplication keys on that ID so even if the same article is delivered
twice across a websocket reconnect it isn't traded again.

The window (default 5 minutes) is much shorter than the bracket-order
lifetime (hours to days) but long enough to suppress duplicate websocket
deliveries.  After the window elapses the ID is evicted from memory, so the
deduplicator doesn't grow unboundedly over a multi-day soak.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from brokebyte.ingestion.events import NewsEvent


class EventDeduplicator:
    """Rolling-window deduplication keyed on NewsEvent.id."""

    def __init__(self, window_seconds: int = 300) -> None:
        self._window = timedelta(seconds=window_seconds)
        self._seen: dict[str, datetime] = {}

    def is_duplicate(self, event: NewsEvent) -> bool:
        """Return True if this event.id has been seen within the window.

        Side-effect: records unseen events so subsequent calls for the same ID
        return True."""
        now = datetime.now(timezone.utc)
        self._evict(now)
        if event.id in self._seen:
            return True
        self._seen[event.id] = now
        return False

    def _evict(self, now: datetime) -> None:
        cutoff = now - self._window
        self._seen = {k: v for k, v in self._seen.items() if v >= cutoff}

    @property
    def seen_count(self) -> int:
        """Number of IDs currently in the dedup window (useful for monitoring)."""
        return len(self._seen)
