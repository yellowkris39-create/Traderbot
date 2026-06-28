"""Data-provider interface for the screener.

Locked decision: build behind this interface on yfinance first, swap to a paid
provider (Finnhub / FMP / EOD) later WITHOUT touching rules/sizing/screen code.

This module defines only the contract + value types. The concrete yfinance
implementation (yfinance_provider.py) is Phase 3 — it must handle:
  * >= 250 daily bars (for the 200-day SMA) WITH a volume column,
  * LSE prices quoted in pence (GBp) -> convert to GBP,
  * market cap, beta, and next-earnings date,
  * graceful None when a field is unavailable (callers fail-closed).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import pandas as pd


@dataclass(frozen=True)
class Fundamentals:
    market_cap: float | None
    beta: float | None
    next_earnings: datetime | None
    currency: str            # "GBP" | "USD"


class DataProvider(Protocol):
    """Minimal contract the screener needs from any market-data source."""

    def daily_bars(self, symbol: str, lookback_days: int = 400) -> pd.DataFrame:
        """Oldest-first daily OHLCV (open, high, low, close, volume), prices in
        the instrument's display currency (GBP for LSE, USD for US)."""
        ...

    def fundamentals(self, symbol: str) -> Fundamentals: ...
