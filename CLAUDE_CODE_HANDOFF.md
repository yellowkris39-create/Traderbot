# Claude Code handoff — BrokeByte

All code unit-tested locally (311 tests pass; the 1 local "failure" is a sandbox
SOCKS-proxy artifact in the anthropic client — passes on the server).

## STATUS (verified live on paper 2026-06-28)
- Exit manager works: get_open_stop returned ('f1cac7b4…', 26.0) on a SIRI bracket;
  flatten closed SIRI clean, zero orphans. replace_stop still unverified (needs a
  live position at +1R — it'll exercise itself when one reaches break-even).
- Screener dry-run works end-to-end: 18 symbols, BARC.L=£5.05 (pounds OK), market
  cap £68.1B, GBP/USD 1.3250. No setups today (legit — strict ruleset). Timer armed.
- Repo committed + pushed (999cd06).

## NEW SINCE 999cd06 (uncommitted in my local; pull these)
- `brokebyte/screener/backtest.py` — walk-forward backtest of the ruleset + exit
  ladder; `compute_metrics` reports win rate / avg-R / expectancy.
- `brokebyte/screener/screen.py` — evaluate_symbol gained `skip_universe` (backtest
  can't reconstruct point-in-time fundamentals, so it tests the bar-derived
  trend/setup/trigger only).
- `brokebyte/screener/yfinance_provider.py` — aligned to your `_fi_get` FastInfo fix
  (attribute-first, dict fallback) so my local matches the server.
- `tests/test_backtest_screener.py` (8 tests).

## TASKS
1. Pull/merge the above; `venv/bin/python -m pytest -q` (expect green); commit + push.
2. **Run the historical backtest** (yfinance needs the server's network):
   ```
   cd /root/Trader && venv/bin/python -m brokebyte.screener.backtest
   # or specific names:  venv/bin/python -m brokebyte.screener.backtest AAPL MSFT BARC.L
   ```
   Report the OVERALL line: trades, win rate, avg R, expectancy. That's the
   "is the edge real" number — if expectancy is <= 0 over a decent sample, we
   retune before trusting live.
3. `git checkout -- requirements.txt` (recurring cosmetic BOM diff).

## BACKTEST CAVEATS (built in, stated honestly)
- Entry = next bar open; if a bar touches both stop & target, stop assumed first.
- Break-even/trailing evaluated on close, applied next bar (no intrabar).
- No commission/slippage/spread. Universe filters (cap/beta/earnings) skipped in
  backtest — so live results will be stricter (fewer trades) than the backtest.

## REMAINING FOLLOW-UPS
- Expand universe.py from the starter list to full S&P 500 + FTSE 350.
- (Optional) holiday-aware trading-day count for the time-stop (currently weekdays).
- (Optional) commission/slippage model in the backtest for realism.
