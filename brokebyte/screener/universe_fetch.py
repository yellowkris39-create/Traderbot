"""Fetch current index constituents (S&P 500 + FTSE 350) for the screener.

Constituents change over time and can't be hand-maintained accurately, so we
pull them from Wikipedia at runtime (pandas.read_html) and cache to JSON. The
live fetch runs on the SERVER (its venv has network); my build sandbox can't
reach Wikipedia, so only the pure parsing/normalisation is unit-tested here.

    python -m brokebyte.screener.universe_fetch        # refresh the cache

Ticker normalisation for yfinance:
  * US: dots become hyphens (BRK.B -> BRK-B).
  * LSE: dots become hyphens and a '.L' suffix is added (BT.A -> BT-A.L).
On any fetch failure we DON'T overwrite the cache; callers fall back to the
starter universe.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
FTSE100_URL = "https://en.wikipedia.org/wiki/FTSE_100_Index"
FTSE250_URL = "https://en.wikipedia.org/wiki/FTSE_250_Index"

DEFAULT_CACHE = Path(__file__).with_name("universe_data.json")

_US_COLS = ("Symbol", "Ticker symbol", "Ticker")
_LSE_COLS = ("Ticker", "EPIC", "Symbol", "Code")


def normalize_us_ticker(t: str) -> str:
    return str(t).strip().upper().replace(".", "-")


def normalize_lse_ticker(t: str) -> str:
    base = str(t).strip().upper()
    if base.endswith(".L"):
        base = base[:-2]
    base = base.replace(".", "-")
    return f"{base}.L"


def _pick_column(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def parse_tickers(tables: list[pd.DataFrame], candidates: tuple[str, ...]) -> list[str]:
    """Return the raw ticker column from the first table that has one of
    `candidates`. Pure — operates on already-fetched DataFrames."""
    for df in tables:
        col = _pick_column(df, candidates)
        if col is not None:
            return [str(v) for v in df[col].dropna().tolist()]
    return []


def fetch_us() -> list[str]:
    tables = pd.read_html(SP500_URL)
    return [normalize_us_ticker(t) for t in parse_tickers(tables, _US_COLS)]


def fetch_lse() -> list[str]:
    out: list[str] = []
    for url in (FTSE100_URL, FTSE250_URL):
        try:
            tables = pd.read_html(url)
            out.extend(normalize_lse_ticker(t) for t in parse_tickers(tables, _LSE_COLS))
        except Exception:
            continue
    return out


def refresh(path: Path = DEFAULT_CACHE) -> dict:
    """Fetch + cache. Returns the data dict written. Partial success is kept;
    if BOTH lists are empty the cache is left untouched."""
    us, lse = [], []
    try:
        us = sorted(set(fetch_us()))
    except Exception as exc:  # noqa: BLE001
        print(f"[universe_fetch] US fetch failed: {exc}")
    try:
        lse = sorted(set(fetch_lse()))
    except Exception as exc:  # noqa: BLE001
        print(f"[universe_fetch] LSE fetch failed: {exc}")

    if not us and not lse:
        print("[universe_fetch] nothing fetched; cache left unchanged")
        return {}

    data = {"fetched_at": datetime.now(timezone.utc).isoformat(), "us": us, "lse": lse}
    Path(path).write_text(json.dumps(data, indent=2))
    print(f"[universe_fetch] wrote {len(us)} US + {len(lse)} LSE tickers to {path}")
    return data


if __name__ == "__main__":
    refresh()
