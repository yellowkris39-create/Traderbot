"""Module 2: two-tier Claude provider (Haiku materiality filter -> Sonnet verdict).

Guardrails honored here:
- #2 fail-safe-to-HOLD: any API error or unparseable response yields
  `hold_verdict(...)` (via parsing.py), never a crash or a guessed verdict.
- #5 prompt-injection defense: source text matching an injection pattern
  (Guard 8's regex scan) short-circuits to HOLD *before* it is ever sent to
  the model, in addition to the untrusted-data framing in the prompts and
  the post-verdict symbol-grounding check in guards/grounding.py.

Caching:
- Per-news-ID verdict cache (`VerdictCache`) avoids re-asking about the same
  event within a run.
- Anthropic prompt caching (`cache_control: ephemeral`) is applied to the
  static system prompts so repeat calls are cheap.
"""

from __future__ import annotations

from typing import Protocol

from brokebyte.config import Config
from brokebyte.guards.grounding import check_injection_patterns
from brokebyte.ingestion.events import NewsEvent
from brokebyte.llm.cache import InMemoryVerdictCache, VerdictCache
from brokebyte.llm.parsing import MaterialityResult, hold_verdict, parse_materiality, parse_verdict
from brokebyte.llm.prompts import MATERIALITY_SYSTEM_PROMPT, VERDICT_SYSTEM_PROMPT, build_user_prompt
from brokebyte.llm.provider import Direction, LLMProvider, LLMVerdict, StubLLMProvider, TimeHorizon

DEFAULT_MAX_TOKENS = 1024


class _MessagesClient(Protocol):
    def create(self, **kwargs: object) -> object: ...


class _AnthropicClientLike(Protocol):
    messages: _MessagesClient


class ClaudeProvider(LLMProvider):
    """Two-tier LLMProvider: Haiku materiality filter, Sonnet full verdict."""

    def __init__(
        self,
        client: _AnthropicClientLike,
        haiku_model: str,
        sonnet_model: str,
        cache: VerdictCache | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._client = client
        self._haiku_model = haiku_model
        self._sonnet_model = sonnet_model
        self._cache: VerdictCache = cache if cache is not None else InMemoryVerdictCache()
        self._max_tokens = max_tokens

    def evaluate(self, event: NewsEvent) -> LLMVerdict:
        cached = self._cache.get(event.id)
        if cached is not None:
            return cached

        injection_check = check_injection_patterns(event)
        if not injection_check.ok:
            verdict = hold_verdict(f"rejected before LLM call: {injection_check.reason}")
            self._cache.set(event.id, verdict)
            return verdict

        verdict = self._evaluate_uncached(event)
        self._cache.set(event.id, verdict)
        return verdict

    def _evaluate_uncached(self, event: NewsEvent) -> LLMVerdict:
        try:
            materiality = self._call_materiality(event)
        except Exception as exc:  # noqa: BLE001 - external API call, fail safe
            return hold_verdict(f"materiality call failed: {exc!r}")

        if not materiality.material:
            return LLMVerdict(
                material=False,
                symbol=materiality.symbol,
                direction=Direction.NONE,
                confidence=0.0,
                time_horizon=TimeHorizon.NONE,
                reasoning=materiality.reasoning,
                is_already_priced_in=False,
            )

        try:
            return self._call_verdict(event)
        except Exception as exc:  # noqa: BLE001 - external API call, fail safe
            return hold_verdict(f"verdict call failed: {exc!r}")

    def _call_materiality(self, event: NewsEvent) -> MaterialityResult:
        response = self._client.messages.create(
            model=self._haiku_model,
            max_tokens=self._max_tokens,
            system=[{"type": "text", "text": MATERIALITY_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": build_user_prompt(event)}],
        )
        return parse_materiality(response.content[0].text)

    def _call_verdict(self, event: NewsEvent) -> LLMVerdict:
        response = self._client.messages.create(
            model=self._sonnet_model,
            max_tokens=self._max_tokens,
            system=[{"type": "text", "text": VERDICT_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": build_user_prompt(event)}],
        )
        return parse_verdict(response.content[0].text)


def build_claude_provider(config: Config) -> LLMProvider:
    """Build the real two-tier provider, or an always-HOLD fallback if unconfigured.

    Never falls back to the bullish StubLLMProvider default verdict - a
    missing API key must not result in trading on a fixed/fake signal.
    """
    if not config.anthropic_api_key:
        return StubLLMProvider(hold_verdict("ANTHROPIC_API_KEY not set - failing safe to HOLD"))

    import anthropic

    client = anthropic.Anthropic(api_key=config.anthropic_api_key, timeout=30.0)
    return ClaudeProvider(client, haiku_model=config.llm.haiku_model, sonnet_model=config.llm.sonnet_model)
