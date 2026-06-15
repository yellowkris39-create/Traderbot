"""Guard 8 — Injection / Hallucination Guard.

News text is untrusted input from the open web (real security risk, not
just noise). This guard runs before any LLM verdict can drive a trade:

1. Symbol grounding — reject verdicts naming a ticker the source event
   doesn't carry, since that's either a hallucination or the model having
   been steered off-topic by the text it was given.
2. Injection-pattern scan — reject source text that itself looks like an
   attempt to issue instructions ("ignore previous instructions", etc.)
   before it's allowed to have driven a decision at all.
"""

from __future__ import annotations

import re

from brokebyte.common import CheckResult
from brokebyte.ingestion.events import NewsEvent
from brokebyte.llm.provider import LLMVerdict

_INJECTION_PATTERNS = [
    re.compile(r"ignore (all|any|the )?(previous|prior|above) instructions", re.IGNORECASE),
    re.compile(r"disregard (the|all|your) (system|previous|prior)", re.IGNORECASE),
    re.compile(r"\byou are now\b", re.IGNORECASE),
    re.compile(r"new instructions\s*:", re.IGNORECASE),
    re.compile(r"\bsystem prompt\b", re.IGNORECASE),
    re.compile(r"\bact as\b", re.IGNORECASE),
]


def check_symbol_grounding(verdict: LLMVerdict, event: NewsEvent) -> CheckResult:
    """Reject verdicts naming a symbol the source event doesn't mention."""
    if verdict.symbol is None:
        return CheckResult(True)
    if not event.symbols:
        return CheckResult(False, f"verdict claims symbol {verdict.symbol} but source event has no symbols")
    if verdict.symbol.upper() not in {s.upper() for s in event.symbols}:
        return CheckResult(
            False,
            f"verdict symbol {verdict.symbol} not in source event symbols {event.symbols} (low grounding)",
        )
    return CheckResult(True)


def check_injection_patterns(event: NewsEvent) -> CheckResult:
    """Flag source text containing prompt-injection-style instructions."""
    text = f"{event.headline}\n{event.summary}"
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return CheckResult(False, f"source text matches injection pattern: {pattern.pattern!r}")
    return CheckResult(True)
