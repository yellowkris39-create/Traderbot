"""Tests for brokebyte.ingestion.dedup (EventDeduplicator)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from brokebyte.ingestion.dedup import EventDeduplicator
from brokebyte.ingestion.events import NewsEvent


def make_event(event_id="evt-1", headline="Corp beats earnings"):
    return NewsEvent(id=event_id, headline=headline, summary="Summary.", symbols=["AAPL"], source="test")


# --- basic dedup behaviour ---------------------------------------------------


def test_first_occurrence_is_not_duplicate():
    dedup = EventDeduplicator()
    assert dedup.is_duplicate(make_event("evt-1")) is False


def test_same_id_second_time_is_duplicate():
    dedup = EventDeduplicator()
    dedup.is_duplicate(make_event("evt-1"))
    assert dedup.is_duplicate(make_event("evt-1")) is True


def test_different_ids_are_not_duplicates():
    dedup = EventDeduplicator()
    dedup.is_duplicate(make_event("evt-1"))
    assert dedup.is_duplicate(make_event("evt-2")) is False


def test_multiple_distinct_events_none_duplicate():
    dedup = EventDeduplicator()
    events = [make_event(f"evt-{i}") for i in range(5)]
    results = [dedup.is_duplicate(e) for e in events]
    assert results == [False] * 5


def test_seen_count_tracks_stored_ids():
    dedup = EventDeduplicator()
    dedup.is_duplicate(make_event("evt-1"))
    dedup.is_duplicate(make_event("evt-2"))
    assert dedup.seen_count == 2


# --- window eviction ---------------------------------------------------------


def test_eviction_after_window_allows_same_id_again():
    dedup = EventDeduplicator(window_seconds=60)
    event = make_event("evt-1")

    # Simulate first seen at T=0
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    with patch("brokebyte.ingestion.dedup.datetime") as mock_dt:
        mock_dt.now.return_value = t0
        dedup.is_duplicate(event)

    # Check again at T=61s (past the 60s window)
    t1 = t0 + timedelta(seconds=61)
    with patch("brokebyte.ingestion.dedup.datetime") as mock_dt:
        mock_dt.now.return_value = t1
        result = dedup.is_duplicate(event)

    assert result is False  # evicted, accepted again


def test_within_window_is_still_duplicate():
    dedup = EventDeduplicator(window_seconds=60)
    event = make_event("evt-1")

    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    with patch("brokebyte.ingestion.dedup.datetime") as mock_dt:
        mock_dt.now.return_value = t0
        dedup.is_duplicate(event)

    # Check at T=30s (still within window)
    t1 = t0 + timedelta(seconds=30)
    with patch("brokebyte.ingestion.dedup.datetime") as mock_dt:
        mock_dt.now.return_value = t1
        result = dedup.is_duplicate(event)

    assert result is True


def test_eviction_removes_expired_entries_from_seen_count():
    dedup = EventDeduplicator(window_seconds=60)
    event = make_event("evt-1")

    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    with patch("brokebyte.ingestion.dedup.datetime") as mock_dt:
        mock_dt.now.return_value = t0
        dedup.is_duplicate(event)

    assert dedup.seen_count == 1

    t1 = t0 + timedelta(seconds=61)
    with patch("brokebyte.ingestion.dedup.datetime") as mock_dt:
        mock_dt.now.return_value = t1
        dedup.is_duplicate(make_event("evt-2"))  # triggers eviction

    assert dedup.seen_count == 1  # evt-1 evicted, evt-2 added
