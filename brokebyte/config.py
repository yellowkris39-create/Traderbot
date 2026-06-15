"""Central configuration, loaded from environment variables (.env).

Guardrail: TRADING_MODE defaults to "paper" and there is no code path that
silently flips it to "live". Live mode requires both TRADING_MODE=live AND
LIVE_TRADING_CONFIRM set to the exact phrase below — a deliberate,
hard-to-fumble action, not a default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

VALID_MODES = ("paper", "live")
LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_LIVE_TRADING_RISK"

# Module 2: provider-agnostic by design — swap either model via env vars,
# no code changes needed.
DEFAULT_HAIKU_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_SONNET_MODEL = "claude-sonnet-4-6"


@dataclass(frozen=True)
class AlpacaCredentials:
    api_key: str
    secret_key: str


@dataclass(frozen=True)
class LLMConfig:
    haiku_model: str
    sonnet_model: str


@dataclass(frozen=True)
class Config:
    trading_mode: str
    alpaca: AlpacaCredentials
    anthropic_api_key: str | None
    llm: LLMConfig
    log_dir: Path

    @property
    def is_paper(self) -> bool:
        return self.trading_mode == "paper"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_config() -> Config:
    """Load and validate configuration. Raises on any unsafe/incomplete setup."""
    mode = os.environ.get("TRADING_MODE", "paper").strip().lower()
    if mode not in VALID_MODES:
        raise RuntimeError(f"TRADING_MODE must be one of {VALID_MODES}, got {mode!r}")

    if mode == "live":
        confirm = os.environ.get("LIVE_TRADING_CONFIRM", "")
        if confirm != LIVE_CONFIRM_PHRASE:
            raise RuntimeError(
                "TRADING_MODE=live requires LIVE_TRADING_CONFIRM="
                f"{LIVE_CONFIRM_PHRASE!r} to be set explicitly. "
                "Refusing to start in live mode without it."
            )
        alpaca = AlpacaCredentials(
            api_key=_require_env("ALPACA_LIVE_API_KEY"),
            secret_key=_require_env("ALPACA_LIVE_SECRET_KEY"),
        )
    else:
        alpaca = AlpacaCredentials(
            api_key=_require_env("ALPACA_PAPER_API_KEY"),
            secret_key=_require_env("ALPACA_PAPER_SECRET_KEY"),
        )

    log_dir = Path(os.environ.get("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    llm = LLMConfig(
        haiku_model=os.environ.get("LLM_HAIKU_MODEL") or DEFAULT_HAIKU_MODEL,
        sonnet_model=os.environ.get("LLM_SONNET_MODEL") or DEFAULT_SONNET_MODEL,
    )

    return Config(
        trading_mode=mode,
        alpaca=alpaca,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        llm=llm,
        log_dir=log_dir,
    )
