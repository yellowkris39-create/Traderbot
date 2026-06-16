"""Live news stream bridge (Phase 6).

Wraps Alpaca's async NewsDataStream in a background daemon thread and
surfaces events to the synchronous main loop via a thread-safe queue.
This keeps all existing synchronous pipeline code unchanged.

Usage:
    stream = NewsStream(config)
    stream.start()
    while True:
        event = stream.get(timeout=60.0)  # None on timeout
        if event is not None:
            process(event)

The stream subscribes to ALL symbols ("*") and lets the two-tier LLM
filter (Module 2 / materiality guard) decide what's worth acting on.
An internal EventDeduplicator suppresses duplicate deliveries across
websocket reconnects (same article ID within a 5-minute window).

Call stream.stop() for a clean shutdown (e.g., on SIGTERM or
KeyboardInterrupt) — this closes the websocket gracefully.
"""

from __future__ import annotations

import queue
import threading

from alpaca.data.live import NewsDataStream

from brokebyte.config import Config
from brokebyte.ingestion.dedup import EventDeduplicator
from brokebyte.ingestion.events import NewsEvent, from_alpaca_news
from brokebyte.logging_setup import get_logger

_QUEUE_MAXSIZE = 200


class NewsStream:
    """Thread-safe bridge from Alpaca's async news websocket to a sync queue."""

    def __init__(self, config: Config, dedup_window_seconds: int = 300) -> None:
        self._queue: queue.Queue[NewsEvent] = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._dedup = EventDeduplicator(window_seconds=dedup_window_seconds)
        self._log = get_logger("brokebyte.ingestion.stream")
        self._stream = NewsDataStream(
            api_key=config.alpaca.api_key,
            secret_key=config.alpaca.secret_key,
        )
        self._stream.subscribe_news(self._handler, "*")

    async def _handler(self, news) -> None:
        try:
            event = from_alpaca_news(news)
        except Exception as exc:
            self._log.warning("news_parse_error", error=str(exc))
            return

        if self._dedup.is_duplicate(event):
            self._log.debug("news_deduplicated", event_id=event.id)
            return

        self._log.info(
            "news_received",
            event_id=event.id,
            headline=event.headline[:80],
            symbols=event.symbols,
            source=event.source,
        )
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._log.warning(
                "news_queue_full_dropping_event",
                event_id=event.id,
                queue_size=self._queue.qsize(),
            )

    def start(self) -> None:
        """Start the websocket stream in a background daemon thread."""
        thread = threading.Thread(target=self._stream.run, daemon=True, name="news-stream")
        thread.start()
        self._log.info("news_stream_thread_started")

    def stop(self) -> None:
        """Shut down the websocket gracefully."""
        try:
            self._stream.stop()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("news_stream_stop_error", error=str(exc))

    def get(self, timeout: float = 1.0) -> NewsEvent | None:
        """Return the next queued event, or None if the timeout elapses."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
