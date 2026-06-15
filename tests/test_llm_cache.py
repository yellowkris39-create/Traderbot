from brokebyte.llm.cache import InMemoryVerdictCache
from brokebyte.llm.provider import Direction, LLMVerdict, TimeHorizon


def make_verdict(**overrides):
    defaults = dict(
        material=True,
        symbol="AAPL",
        direction=Direction.LONG,
        confidence=0.8,
        time_horizon=TimeHorizon.SWING,
        reasoning="test",
        is_already_priced_in=False,
    )
    defaults.update(overrides)
    return LLMVerdict(**defaults)


def test_get_missing_key_returns_none():
    cache = InMemoryVerdictCache()

    assert cache.get("evt-1") is None


def test_set_then_get_roundtrip():
    cache = InMemoryVerdictCache()
    verdict = make_verdict()

    cache.set("evt-1", verdict)

    assert cache.get("evt-1") == verdict


def test_distinct_event_ids_are_independent():
    cache = InMemoryVerdictCache()
    verdict_a = make_verdict(symbol="AAPL")
    verdict_b = make_verdict(symbol="TSLA")

    cache.set("evt-1", verdict_a)
    cache.set("evt-2", verdict_b)

    assert cache.get("evt-1") == verdict_a
    assert cache.get("evt-2") == verdict_b


def test_set_overwrites_existing_entry():
    cache = InMemoryVerdictCache()
    cache.set("evt-1", make_verdict(symbol="AAPL"))

    cache.set("evt-1", make_verdict(symbol="TSLA"))

    assert cache.get("evt-1").symbol == "TSLA"
