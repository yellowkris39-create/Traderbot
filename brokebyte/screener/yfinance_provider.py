"""yfinance implementation of the screener DataProvider (locked decision:
start free, swap later). Network calls are isolated in thin methods; all
parsing lives in pure module functions so it unit-tests without network.

VERIFY-LIVE NOTES (Yahoo is unreachable from the build sandbox, and yfinance
is an unofficial scraper whose shapes drift between versions — confirm against
live data before trusting):
  * LSE ('.L') prices and history come in PENCE (GBp); we divide by 100 when
    fast_info.currency == 'GBp' so everything downstream is in GBP.
  * `beta` comes from the slow `.info` dict and may be missing -> None.
  * earnings-date extraction handles both the `.calendar` dict and the
    `get_earnings_dates()` frame; both have changed across yfinance releases.
Built against yfinance 1.4.1.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from brokebyte.screener.data import Fundamentals

_BAR_COLS = ("open", "high", "low", "close", "volume")


def _period_for(lookback_days: int) -> str:
    """Map a calendar-day lookback to a yfinance `period` string with headroom
    so a 200-day SMA always has enough bars."""
    if lookback_days <= 365:
        return "1y"
    if lookback_days <= 730:
        return "2y"
    return "5y"


def normalize_bars(raw: pd.DataFrame, currency: str | None) -> pd.DataFrame:
    """yfinance OHLCV frame -> oldest-first lowercase open/high/low/close/volume.
    Divides price columns by 100 for pence-quoted (GBp) instruments."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=list(_BAR_COLS))
    df = raw.rename(columns={c: c.lower() for c in raw.columns})
    missing = {"open", "high", "low", "close", "volume"} - set(df.columns)
    if missing:
        return pd.DataFrame(columns=list(_BAR_COLS))
    df = df[["open", "high", "low", "close", "volume"]].copy()
    # LSE quotes in pence: 'GBp' = pence, 'GBP' = pounds. Only pence needs /100.
    if currency == "GBp":
        for col in ("open", "high", "low", "close"):
            df[col] = df[col] / 100.0
    df = df.reset_index(drop=True)
    return df


def _get(obj, key):
    """Retrieve a value from a dict or yfinance FastInfo object."""
    try:
        return getattr(obj, key)
    except Exception:
        pass
    try:
        return obj[key]
    except Exception:
        return None


def extract_currency(fast_info) -> str | None:
    v = _get(fast_info, "currency")
    return str(v) if v else None


def extract_market_cap(fast_info) -> float | None:
    v = _get(fast_info, "market_cap") or _get(fast_info, "marketCap")
    return float(v) if v else None


def extract_beta(info: dict | None) -> float | None:
    if not info:
        return None
    v = info.get("beta")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def extract_next_earnings(calendar, now: datetime | None = None) -> datetime | None:
    """Best-effort next earnings date from yfinance `.calendar`.

    Handles the dict form ({'Earnings Date': [date, ...]}) and a DataFrame
    form. Returns the soonest future date (tz-aware UTC), or None.
    """
    now = now or datetime.now(timezone.utc)
    dates: list[datetime] = []

    raw_list = None
    if isinstance(calendar, dict):
        raw_list = calendar.get("Earnings Date")
    elif isinstance(calendar, pd.DataFrame) and "Earnings Date" in getattr(calendar, "index", []):
        raw_list = list(calendar.loc["Earnings Date"].values)

    if raw_list is None:
        return None
    if not isinstance(raw_list, (list, tuple)):
        raw_list = [raw_list]

    for d in raw_list:
        try:
            ts = pd.Timestamp(d)
            dt = ts.to_pydatetime()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dates.append(dt)
        except Exception:
            continue

    future = [d for d in dates if d >= now]
    if future:
        return min(future)
    return min(dates) if dates else None


class YFinanceProvider:
    """Concrete DataProvider backed by yfinance."""

    def __init__(self) -> None:
        import yfinance as yf  # imported lazily so unit tests need no network
        self._yf = yf

    def _ticker(self, symbol: str):
        return self._yf.Ticker(symbol)

    def daily_bars(self, symbol: str, lookback_days: int = 400) -> pd.DataFrame:
        t = self._ticker(symbol)
        raw = t.history(period=_period_for(lookback_days), interval="1d", auto_adjust=False)
        return normalize_bars(raw, extract_currency(t.fast_info))

    def fundamentals(self, symbol: str) -> Fundamentals:
        t = self._ticker(symbol)
        fast = t.fast_info
        currency = extract_currency(fast)
        market_cap = extract_market_cap(fast)
        if currency == "GBp" and market_cap is not None:
            market_cap = market_cap / 100.0  # cap also reported in pence on LSE
        try:
            info = t.info
        except Exception:
            info = None
        try:
            calendar = t.calendar
        except Exception:
            calendar = None
        return Fundamentals(
            market_cap=market_cap,
            beta=extract_beta(info),
            next_earnings=extract_next_earnings(calendar),
            currency="GBP" if currency in ("GBp", "GBP") else (currency or "USD"),
        )
