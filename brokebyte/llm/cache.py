"""Per-news-ID verdict cache.

Distinct from Anthropic prompt caching (which caches the static system
prompt across calls): this avoids re-asking the model about the same news
item more than once within a run. In-memory only for now; a persistent
implementation (Module 7) can satisfy the same protocol later.
"""

from __future__ import annotations

from typing import Protocol

from brokebyte.llm.provider import LLMVerdict


class VerdictCache(Protocol):
    def get(self, event_id: str) -> LLMVerdict | None: ...

    def set(self, event_id: str, verdict: LLMVerdict) -> None: ...


class InMemoryVerdictCache:
    def __init__(self) -> None:
        self._store: dict[str, LLMVerdict] = {}

    def get(self, event_id: str) -> LLMVerdict | None:
        return self._store.get(event_id)

    def set(self, event_id: str, verdict: LLMVerdict) -> None:
        self._store[event_id] = verdict
