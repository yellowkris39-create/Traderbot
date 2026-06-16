"""Tests for brokebyte.ingestion.events — from_alpaca_news() factory."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from brokebyte.ingestion.events import NewsEvent, from_alpaca_news


def make_alpaca_news(**overrides):
    """Build a mock Alpaca News object matching the real News model shape."""
    defaults = dict(
        id=12345,
        headline="Acme Corp beats Q2 earnings",
        summary="Acme reported record revenue for Q2.",
        source="Benzinga",
        symbols=["ACME", "ACM"],
        created_at=datetime(2026, 6, 16, 9, 30, 0, tzinfo=timezone.utc),
        url="https://example.com/news/12345",
        updated_at=datetime(2026, 6, 16, 9, 31, 0, tzinfo=timezone.utc),
        author="Jane Doe",
        content="<p>Full article content...</p>",
    )
    defaults.update(overrides)
    news = MagicMock()
    for k, v in defaults.items():
        setattr(news, k, v)
    return news


def test_from_alpaca_news_converts_int_id_to_str():
    news = make_alpaca_news(id=99999)
    event = from_alpaca_news(news)
    assert event.id == "99999"


def test_from_alpaca_news_maps_headline():
    news = make_alpaca_news(headline="Big announcement from Corp X")
    event = from_alpaca_news(news)
    assert event.headline == "Big announcement from Corp X"


def test_from_alpaca_news_maps_summary():
    news = make_alpaca_news(summary="The announcement details here.")
    event = from_alpaca_news(news)
    assert event.summary == "The announcement details here."


def test_from_alpaca_news_maps_symbols():
    news = make_alpaca_news(symbols=["AAPL", "MSFT"])
    event = from_alpaca_news(news)
    assert event.symbols == ["AAPL", "MSFT"]


def test_from_alpaca_news_maps_source():
    news = make_alpaca_news(source="Benzinga")
    event = from_alpaca_news(news)
    assert event.source == "Benzinga"


def test_from_alpaca_news_maps_created_at():
    ts = datetime(2026, 6, 16, 9, 30, 0, tzinfo=timezone.utc)
    news = make_alpaca_news(created_at=ts)
    event = from_alpaca_news(news)
    assert event.created_at == ts


def test_from_alpaca_news_returns_news_event():
    news = make_alpaca_news()
    event = from_alpaca_news(news)
    assert isinstance(event, NewsEvent)


def test_from_alpaca_news_empty_symbols_list():
    news = make_alpaca_news(symbols=[])
    event = from_alpaca_news(news)
    assert event.symbols == []


def test_from_alpaca_news_none_summary_becomes_empty_string():
    news = make_alpaca_news(summary=None)
    event = from_alpaca_news(news)
    assert event.summary == ""


def test_from_alpaca_news_none_source_becomes_alpaca():
    news = make_alpaca_news(source=None)
    event = from_alpaca_news(news)
    assert event.source == "alpaca"


def test_from_alpaca_news_none_created_at_uses_now():
    news = make_alpaca_news(created_at=None)
    event = from_alpaca_news(news)
    assert event.created_at is not None
    assert event.created_at.tzinfo is not None
