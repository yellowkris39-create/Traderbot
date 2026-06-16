from brokebyte.ingestion.events import NewsEvent
from brokebyte.llm.prompts import MATERIALITY_SYSTEM_PROMPT, VERDICT_SYSTEM_PROMPT, build_user_prompt


def make_event(**overrides):
    defaults = dict(
        id="evt-1",
        headline="Example Corp announces new product line",
        summary="Routine product announcement with no financial details.",
        symbols=["AAPL"],
        source="test",
    )
    defaults.update(overrides)
    return NewsEvent(**defaults)


# --- injection-defense framing -------------------------------------------


def test_materiality_prompt_frames_news_as_untrusted_data():
    assert "untrusted" in MATERIALITY_SYSTEM_PROMPT.lower()
    assert "DATA ONLY" in MATERIALITY_SYSTEM_PROMPT
    assert "ignore" in MATERIALITY_SYSTEM_PROMPT.lower()


def test_verdict_prompt_frames_news_as_untrusted_data():
    assert "untrusted" in VERDICT_SYSTEM_PROMPT.lower()
    assert "DATA ONLY" in VERDICT_SYSTEM_PROMPT
    assert "ignore" in VERDICT_SYSTEM_PROMPT.lower()


# --- JSON schema framing ---------------------------------------------------


def test_materiality_prompt_specifies_expected_fields():
    for field in ("material", "symbol", "reasoning"):
        assert f'"{field}"' in MATERIALITY_SYSTEM_PROMPT


def test_verdict_prompt_specifies_expected_fields():
    for field in (
        "material",
        "symbol",
        "direction",
        "confidence",
        "time_horizon",
        "reasoning",
        "is_already_priced_in",
    ):
        assert f'"{field}"' in VERDICT_SYSTEM_PROMPT


def test_verdict_prompt_specifies_enum_values():
    assert '"long"' in VERDICT_SYSTEM_PROMPT
    assert '"short"' in VERDICT_SYSTEM_PROMPT
    assert '"intraday"' in VERDICT_SYSTEM_PROMPT
    assert '"swing"' in VERDICT_SYSTEM_PROMPT


# --- user prompt -------------------------------------------------------------


def test_user_prompt_includes_event_fields():
    event = make_event(headline="Acme beats earnings", summary="Big quarter for Acme.", symbols=["ACME", "ACM"])

    prompt = build_user_prompt(event)

    assert "Acme beats earnings" in prompt
    assert "Big quarter for Acme." in prompt
    assert "ACME, ACM" in prompt
    assert event.id in prompt


def test_user_prompt_handles_no_tagged_symbols():
    event = make_event(symbols=[])

    prompt = build_user_prompt(event)

    assert "(none tagged)" in prompt


def test_user_prompt_with_historical_context_includes_block():
    event = make_event()
    ctx = "Past setups in the same market regime (3 most recent closed trades):\n  1. ..."

    prompt = build_user_prompt(event, historical_context=ctx)

    assert "<historical_context>" in prompt
    assert ctx in prompt
    assert "</historical_context>" in prompt


def test_user_prompt_without_historical_context_has_no_block():
    event = make_event()

    prompt = build_user_prompt(event)

    assert "<historical_context>" not in prompt


def test_user_prompt_historical_context_placed_before_analysis_instruction():
    event = make_event()
    ctx = "some context"

    prompt = build_user_prompt(event, historical_context=ctx)

    ctx_pos = prompt.index("<historical_context>")
    instruction_pos = prompt.index("Analyze the news_item")
    assert ctx_pos < instruction_pos
