"""Provider-agnostic LLM reasoning interface.

Concrete providers (Claude Haiku/Sonnet two-tier, etc.) plug in behind
LLMProvider in Phase 3. For Milestone 1, StubLLMProvider returns a fixed
verdict so the rest of the pipeline can be exercised without a live model.

The verdict shape matches the spec's required strict-JSON output:
{material, symbol, direction, confidence, time_horizon, reasoning,
is_already_priced_in}.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from brokebyte.ingestion.events import NewsEvent


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    NONE = "none"


class TimeHorizon(str, Enum):
    INTRADAY = "intraday"
    SWING = "swing"
    NONE = "none"


@dataclass(frozen=True)
class LLMVerdict:
    material: bool
    symbol: str | None
    direction: Direction
    confidence: float  # 0.0-1.0
    time_horizon: TimeHorizon
    reasoning: str
    is_already_priced_in: bool


class LLMProvider(ABC):
    """Provider-agnostic interface for the LLM reasoning stage."""

    @abstractmethod
    def evaluate(self, event: NewsEvent) -> LLMVerdict:
        """Return a structured verdict for a single news event."""


class StubLLMProvider(LLMProvider):
    """Fixed-verdict provider for Milestone 1 plumbing.

    Always returns the verdict supplied at construction time, regardless
    of the event passed in. Replaced by the Haiku/Sonnet two-tier provider
    in Phase 3.
    """

    def __init__(self, verdict: LLMVerdict) -> None:
        self._verdict = verdict

    def evaluate(self, event: NewsEvent) -> LLMVerdict:
        return self._verdict
