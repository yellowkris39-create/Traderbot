"""Screener universe (starter).

A curated, version-controlled ticker list is more robust than scraping a live
screener. This is a SMALL starter set for wiring/testing only; Phase 3 will
expand it to the S&P 500 + FTSE 350 constituents (generated, then committed).

US tickers are plain symbols; LSE tickers use the yfinance '.L' suffix.
"""

from __future__ import annotations

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
