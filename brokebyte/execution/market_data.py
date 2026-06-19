"""Wraps StockHistoricalDataClient for the market data the risk gate needs:
daily bars for ATR/regime (Modules 4 & 9) and a live quote for sizing and
the liquidity/spread guard (Module 10).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame

from brokebyte.common import Quote
from brokebyte.config import Config

_T = TypeVar("_T")

_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 1.0


def _retry(fn: Callable[[], _T], retries: int = _MAX_RETRIES, delay: float = _RETRY_DELAY_SECONDS) -> _T:
    """Call fn(), retrying up to `retries` times with linear backoff on any exception."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    raise last_exc  # type: ignore[misc]


class MarketData:
    def __init__(self, config: Config) -> None:
        self._client = StockHistoricalDataClient(
            api_key=config.alpaca.api_key,
            secret_key=config.alpaca.secret_key,
        )

    def get_daily_bars(self, symbol: str, lookback_days: int = 100) -> pd.DataFrame:
        """Oldest-first daily bars with `high`, `low`, `close` columns,
        covering roughly `lookback_days` calendar days."""
        _REQUIRED_COLS = {"open", "high", "low", "close"}

        def _fetch() -> pd.DataFrame:
            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=datetime.now(timezone.utc) - timedelta(days=lookback_days),
            )
            bar_set = self._client.get_stock_bars(request)
            df = bar_set.df
            if df.empty:
                return pd.DataFrame()

            result = df.loc[symbol].reset_index(drop=True)

            # .loc[symbol] returns a Series when there is exactly one bar —
            # convert it back to a single-row DataFrame so callers always
            # receive a consistent shape.
            if isinstance(result, pd.Series):
                result = result.to_frame().T.reset_index(drop=True)

            # Reject frames missing any required OHLC column — the pipeline
            # would crash with a KeyError inside atr()/sma() otherwise.
            if not _REQUIRED_COLS.issubset(result.columns):
                return pd.DataFrame()

            return result

        return _retry(_fetch)

    def get_historical_bars(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Oldest-first daily bars with `open`, `high`, `low`, `close`,
        `volume`, and `timestamp` columns over [start, end], for backtesting
        (brokebyte.backtest)."""
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
        )
        bar_set = self._client.get_stock_bars(request)
        df = bar_set.df
        if df.empty:
            return df
        return df.loc[symbol].reset_index()

    def get_quote(self, symbol: str) -> Quote:
        def _fetch() -> Quote:
            request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = self._client.get_stock_latest_quote(request)
            quote = quotes[symbol]
            return Quote(bid_price=float(quote.bid_price), ask_price=float(quote.ask_price))

        return _retry(_fetch)
