"""Screener universe.

Prefers the cached full constituent list written by universe_fetch.refresh()
(S&P 500 + FTSE 350); falls back to a small hand-kept STARTER set when the
cache is absent (fresh checkout, or a failed fetch). US tickers are plain
symbols; LSE tickers use the yfinance '.L' suffix.
"""

from __future__ import annotations

import json
from pathlib import Path

_CACHE = Path(__file__).with_name("universe_data.json")

US_STARTER: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "V", "UNH", "HD",
)

LSE_STARTER: tuple[str, ...] = (
    "SHEL.L", "AZN.L", "HSBA.L", "ULVR.L", "BP.L", "GSK.L", "RIO.L", "BATS.L",
)


def starter_universe(include_us: bool = True, include_lse: bool = True) -> list[str]:
    out: list[str] = []
    if include_us:
        out.extend(US_STARTER)
    if include_lse:
        out.extend(LSE_STARTER)
    return out


def load_universe(include_us: bool = True, include_lse: bool = True,
                  cache: Path = _CACHE) -> list[str]:
    """Full cached universe if available, else the starter set."""
    try:
        if Path(cache).exists():
            data = json.loads(Path(cache).read_text())
            out: list[str] = []
            if include_us:
                out.extend(data.get("us", []))
            if include_lse:
                out.extend(data.get("lse", []))
            if out:
                return out
    except Exception:
        pass
    return starter_universe(include_us=include_us, include_lse=include_lse)
