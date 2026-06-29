"""Tests for universe ticker normalisation, parsing, and cache load/fallback."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from brokebyte.screener import universe, universe_fetch as uf


def test_normalize_us_ticker():
    assert uf.normalize_us_ticker("BRK.B") == "BRK-B"
    assert uf.normalize_us_ticker(" aapl ") == "AAPL"


def test_normalize_lse_ticker():
    assert uf.normalize_lse_ticker("BARC") == "BARC.L"
    assert uf.normalize_lse_ticker("BT.A") == "BT-A.L"
    assert uf.normalize_lse_ticker("HSBA.L") == "HSBA.L"  # already suffixed


def test_parse_tickers_picks_right_column():
    t1 = pd.DataFrame({"Other": [1, 2]})
    t2 = pd.DataFrame({"Symbol": ["AAPL", "MSFT"], "Security": ["a", "b"]})
    assert uf.parse_tickers([t1, t2], uf._US_COLS) == ["AAPL", "MSFT"]


def test_parse_tickers_none_found():
    assert uf.parse_tickers([pd.DataFrame({"X": [1]})], uf._US_COLS) == []


def test_load_universe_uses_cache(tmp_path):
    cache = tmp_path / "u.json"
    cache.write_text(json.dumps({"us": ["AAA", "BBB"], "lse": ["CCC.L"]}))
    assert universe.load_universe(cache=cache) == ["AAA", "BBB", "CCC.L"]
    assert universe.load_universe(include_lse=False, cache=cache) == ["AAA", "BBB"]


def test_load_universe_falls_back_when_no_cache(tmp_path):
    missing = tmp_path / "nope.json"
    assert universe.load_universe(cache=missing) == universe.starter_universe()


def test_load_universe_falls_back_on_empty_cache(tmp_path):
    cache = tmp_path / "empty.json"
    cache.write_text(json.dumps({"us": [], "lse": []}))
    assert universe.load_universe(cache=cache) == universe.starter_universe()
