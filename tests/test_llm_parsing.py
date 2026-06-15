import json

from brokebyte.llm.parsing import hold_verdict, parse_materiality, parse_verdict
from brokebyte.llm.provider import Direction, TimeHorizon


def assert_is_hold(verdict):
    assert verdict.material is False
    assert verdict.symbol is None
    assert verdict.direction == Direction.NONE
    assert verdict.confidence == 0.0
    assert verdict.time_horizon == TimeHorizon.NONE
    assert verdict.is_already_priced_in is False


# --- hold_verdict -----------------------------------------------------------


def test_hold_verdict_is_neutral():
    verdict = hold_verdict("some reason")

    assert_is_hold(verdict)
    assert verdict.reasoning == "some reason"


# --- parse_materiality -------------------------------------------------------


def test_parse_materiality_valid_json():
    raw = json.dumps({"material": True, "symbol": "aapl", "reasoning": "Earnings beat"})

    result = parse_materiality(raw)

    assert result.material is True
    assert result.symbol == "AAPL"
    assert result.reasoning == "Earnings beat"


def test_parse_materiality_strips_markdown_code_fence():
    raw = '```json\n{"material": false, "symbol": null, "reasoning": "routine"}\n```'

    result = parse_materiality(raw)

    assert result.material is False
    assert result.symbol is None
    assert result.reasoning == "routine"


def test_parse_materiality_missing_material_key_fails_safe():
    raw = json.dumps({"symbol": "AAPL", "reasoning": "missing material field"})

    result = parse_materiality(raw)

    assert result.material is False
    assert result.symbol is None
    assert "parse error" in result.reasoning


def test_parse_materiality_non_bool_material_fails_safe():
    raw = json.dumps({"material": "true", "symbol": "AAPL", "reasoning": "string not bool"})

    result = parse_materiality(raw)

    assert result.material is False
    assert "parse error" in result.reasoning


def test_parse_materiality_garbage_fails_safe():
    result = parse_materiality("not json at all")

    assert result.material is False
    assert result.symbol is None


def test_parse_materiality_null_symbol():
    raw = json.dumps({"material": True, "symbol": None, "reasoning": "material but unclear which symbol"})

    result = parse_materiality(raw)

    assert result.symbol is None


# --- parse_verdict ------------------------------------------------------------


def _valid_verdict_payload(**overrides):
    payload = {
        "material": True,
        "symbol": "aapl",
        "direction": "long",
        "confidence": 0.75,
        "time_horizon": "swing",
        "reasoning": "Strong earnings beat, likely sustained move.",
        "is_already_priced_in": False,
    }
    payload.update(overrides)
    return payload


def test_parse_verdict_valid_json():
    raw = json.dumps(_valid_verdict_payload())

    verdict = parse_verdict(raw)

    assert verdict.material is True
    assert verdict.symbol == "AAPL"
    assert verdict.direction == Direction.LONG
    assert verdict.confidence == 0.75
    assert verdict.time_horizon == TimeHorizon.SWING
    assert verdict.reasoning == "Strong earnings beat, likely sustained move."
    assert verdict.is_already_priced_in is False


def test_parse_verdict_strips_markdown_code_fence():
    raw = "```\n" + json.dumps(_valid_verdict_payload()) + "\n```"

    verdict = parse_verdict(raw)

    assert verdict.symbol == "AAPL"
    assert verdict.direction == Direction.LONG


def test_parse_verdict_case_insensitive_enums():
    raw = json.dumps(_valid_verdict_payload(direction="SHORT", time_horizon="INTRADAY"))

    verdict = parse_verdict(raw)

    assert verdict.direction == Direction.SHORT
    assert verdict.time_horizon == TimeHorizon.INTRADAY


def test_parse_verdict_clamps_confidence_above_one():
    raw = json.dumps(_valid_verdict_payload(confidence=1.5))

    verdict = parse_verdict(raw)

    assert verdict.confidence == 1.0


def test_parse_verdict_clamps_confidence_below_zero():
    raw = json.dumps(_valid_verdict_payload(confidence=-0.5))

    verdict = parse_verdict(raw)

    assert verdict.confidence == 0.0


def test_parse_verdict_defaults_missing_priced_in_to_false():
    payload = _valid_verdict_payload()
    del payload["is_already_priced_in"]
    raw = json.dumps(payload)

    verdict = parse_verdict(raw)

    assert verdict.is_already_priced_in is False


def test_parse_verdict_non_bool_priced_in_defaults_to_false():
    raw = json.dumps(_valid_verdict_payload(is_already_priced_in="yes"))

    verdict = parse_verdict(raw)

    assert verdict.is_already_priced_in is False


def test_parse_verdict_invalid_direction_fails_safe():
    raw = json.dumps(_valid_verdict_payload(direction="buy"))

    verdict = parse_verdict(raw)

    assert_is_hold(verdict)
    assert "parse error" in verdict.reasoning


def test_parse_verdict_invalid_time_horizon_fails_safe():
    raw = json.dumps(_valid_verdict_payload(time_horizon="next_week"))

    verdict = parse_verdict(raw)

    assert_is_hold(verdict)


def test_parse_verdict_missing_direction_key_fails_safe():
    payload = _valid_verdict_payload()
    del payload["direction"]
    raw = json.dumps(payload)

    verdict = parse_verdict(raw)

    assert_is_hold(verdict)


def test_parse_verdict_non_bool_material_fails_safe():
    raw = json.dumps(_valid_verdict_payload(material="yes"))

    verdict = parse_verdict(raw)

    assert_is_hold(verdict)


def test_parse_verdict_garbage_fails_safe():
    verdict = parse_verdict("the model said something unexpected, not JSON")

    assert_is_hold(verdict)
    assert "parse error" in verdict.reasoning


def test_parse_verdict_null_symbol():
    raw = json.dumps(_valid_verdict_payload(symbol=None, direction="none", time_horizon="none"))

    verdict = parse_verdict(raw)

    assert verdict.symbol is None
