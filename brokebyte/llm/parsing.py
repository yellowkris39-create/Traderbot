"""Fail-safe parsing of Module 2 model output into typed verdicts.

Guardrail #2 (fail-safe-to-HOLD): any malformed/unparseable JSON, missing
required field, or out-of-enum value results in `hold_verdict(...)` rather
than a crash or a guessed value. The model is asked for strict JSON, but
this layer never trusts that it complied.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from brokebyte.llm.provider import Direction, LLMVerdict, TimeHorizon

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


@dataclass(frozen=True)
class MaterialityResult:
    material: bool
    symbol: str | None
    reasoning: str


def hold_verdict(reasoning: str) -> LLMVerdict:
    """The neutral, no-trade verdict used whenever output can't be trusted."""
    return LLMVerdict(
        material=False,
        symbol=None,
        direction=Direction.NONE,
        confidence=0.0,
        time_horizon=TimeHorizon.NONE,
        reasoning=reasoning,
        is_already_priced_in=False,
    )


def _extract_json(text: str) -> dict:
    text = text.strip()
    match = _CODE_FENCE_RE.match(text)
    if match:
        text = match.group(1).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise TypeError(f"expected a JSON object, got {type(data).__name__}")
    return data


def _require_bool(data: dict, key: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key!r} must be a bool, got {type(value).__name__}")
    return value


def _parse_symbol(value: object) -> str | None:
    if value is None:
        return None
    symbol = str(value).strip().upper()
    return symbol or None


def _parse_direction(value: object) -> Direction:
    return Direction(str(value).strip().lower())


def _parse_time_horizon(value: object) -> TimeHorizon:
    return TimeHorizon(str(value).strip().lower())


def _parse_confidence(value: object) -> float:
    confidence = float(value)  # type: ignore[arg-type]
    return max(0.0, min(1.0, confidence))


def parse_materiality(raw: str) -> MaterialityResult:
    """Parse the Haiku materiality-filter response, fail-safe on any issue."""
    try:
        data = _extract_json(raw)
        return MaterialityResult(
            material=_require_bool(data, "material"),
            symbol=_parse_symbol(data.get("symbol")),
            reasoning=str(data.get("reasoning", "")),
        )
    except (KeyError, ValueError, TypeError) as exc:
        return MaterialityResult(material=False, symbol=None, reasoning=f"materiality parse error: {exc!r}")


def parse_verdict(raw: str) -> LLMVerdict:
    """Parse the Sonnet full-verdict response, fail-safe (hold) on any issue."""
    try:
        data = _extract_json(raw)
        priced_in = data.get("is_already_priced_in", False)
        return LLMVerdict(
            material=_require_bool(data, "material"),
            symbol=_parse_symbol(data.get("symbol")),
            direction=_parse_direction(data["direction"]),
            confidence=_parse_confidence(data["confidence"]),
            time_horizon=_parse_time_horizon(data["time_horizon"]),
            reasoning=str(data.get("reasoning", "")),
            is_already_priced_in=priced_in if isinstance(priced_in, bool) else False,
        )
    except (KeyError, ValueError, TypeError) as exc:
        return hold_verdict(f"verdict parse error: {exc!r}")
