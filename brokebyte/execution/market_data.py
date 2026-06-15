"""Wraps StockHistoricalDataClient for the market data the risk gate needs:
daily bars for ATR/regime (Modules 4 & 9) and a live quote for sizing and
the liquidity/spread guard (Module 10).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame

from brokebyte.common import Quote
from brokebyte.config import Config


class MarketData:
    def __init__(self, config: Config) -> None:
        self._client = StockHistoricalDataClient(
            api_key=config.alpaca.api_key,
            secret_key=config.alpaca.secret_key,
        )

    def get_daily_bars(self, symbol: str, lookback_days: int = 100) -> pd.DataFrame:
        """Oldest-first daily bars with `high`, `low`, `close` columns,
        covering roughly `lookback_days` calendar days."""
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=datetime.now(timezone.utc) - timedelta(days=lookback_days),
        )
        bar_set = self._client.get_stock_bars(request)
        df = bar_set.df
        if df.empty:
            return df
        return df.loc[symbol].reset_index(drop=True)

    def get_quote(self, symbol: str) -> Quote:
        request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quotes = self._client.get_stock_latest_quote(request)
        quote = quotes[symbol]
        return Quote(bid_price=float(quote.bid_price), ask_price=float(quote.ask_price))
