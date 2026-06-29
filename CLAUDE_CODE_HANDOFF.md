# Claude Code handoff — BrokeByte

All code unit-tested locally (318 tests pass; the 1 local "failure" is a sandbox
SOCKS-proxy artifact in the anthropic client — passes on the server).

## STATUS
- Exit manager + screener verified live on paper (2026-06-28). Repo at 999cd06+.
- First backtest (18-symbol starter universe, ~1y): 3 trades, 66.7% win, +0.16R
  expectancy. Mechanically sound but sample far too small to trust — fix = bigger
  universe (below), then re-run.

## NEW THIS ROUND (pull these)
- `brokebyte/screener/universe_fetch.py` — fetches S&P 500 (Wikipedia) + FTSE 350
  (FTSE 100 + 250) via pandas.read_html, normalises tickers for yfinance
  (BRK.B->BRK-B; BT.A->BT-A.L), caches to `universe_data.json`. Safe fallback:
  partial success kept, total failure leaves the cache untouched.
- `brokebyte/screener/universe.py` — `load_universe()` reads the cache, falls back
  to the starter set. The screener runner (`__main__.py`) now uses it.
- `tests/test_universe.py` (7 tests).
- (Earlier this session, if not yet pulled: backtest.py, screen.py skip_universe,
  yfinance_provider _fi_get sync, test_backtest_screener.py.)

## TASKS
1. Pull/merge; `venv/bin/python -m pytest -q` (expect green); commit + push.
2. **Refresh the universe** (needs server network):
   ```
   cd /root/Trader && venv/bin/pip install lxml
   venv/bin/python -m brokebyte.screener.universe_fetch
   ```
   Expect "wrote ~500 US + ~350 LSE tickers". Sanity-check a few entries in
   `brokebyte/screener/universe_data.json`. Commit the JSON so it's reproducible.
3. **Re-run the backtest on the full universe** for a meaningful sample:
   ```
   venv/bin/python -m brokebyte.screener.backtest $(venv/bin/python -c "from brokebyte.screener.universe import load_universe; print(' '.join(load_universe()))")
   ```
   (or just `python -m brokebyte.screener.backtest` if you wire it to load_universe).
   Report the OVERALL line. With a few hundred names we should get dozens-to-
   hundreds of trades — THAT expectancy is the real go/no-go number.
4. The daily screener already uses load_universe(), so once the cache is committed
   it will screen the full list automatically on the next 21:30 UTC fire.
5. `git checkout -- requirements.txt` (recurring cosmetic BOM diff).

## CAVEATS
- universe_fetch depends on Wikipedia table structure; if a fetch returns 0 tickers,
  the page layout changed — check the column names against _US_COLS / _LSE_COLS.
- Backtest: entry next-open; stop-before-target on straddle bars; break-even/trail
  on closes; NO commission/slippage; universe filters skipped → live will be stricter.

## REMAINING (optional)
- Add commission/slippage to the backtest for realism.
- Holiday-aware trading-day count for the time-stop (currently weekdays).
- Wire backtest __main__ to default to load_universe() instead of starter.
