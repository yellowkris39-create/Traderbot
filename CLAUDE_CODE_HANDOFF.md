# BrokeByte — HANDOFF (2026-07-02): CRITICAL fix to deploy + verify

## 0. CRITICAL — truncated file was COMMITTED & PUSHED (fix is in the working tree)
Commit `c7e2e7c` accidentally committed a linter-truncated
`brokebyte/screener/yfinance_provider.py`: `fx_per_gbp` ends in a bare `t`
(NameError at call time). `Screener.scan()` called `_fx_per_gbp` outside its
try/except, so the FIRST non-GBP symbol past the regime gate CRASHES the whole
nightly screener run. If the server pulled c7e2e7c or later (it did — the
sweep/wf runs used btcache commits after it), the 21:30 UTC screener has been
failing silently since ~2026-06-28: no Telegram message at all (which looks
identical to a quiet day if nobody is counting messages).

Fixed locally (working tree, NOT committed — stale `.git/index.lock` blocks
git from the Cowork sandbox; Kris or you must delete it first):
1. `yfinance_provider.py` restored from `999cd06` (intact fx_per_gbp).
2. `screen.py`: `fx = self._fx_per_gbp(...)` moved INSIDE the per-symbol
   try/except (one bad provider response can no longer kill the scan).
3. NEW `tests/test_yfinance_fx.py`: 4 fx_per_gbp unit tests + a tripwire test
   that fails if ANY brokebyte function body ends in a bare name / any module
   lacks a trailing newline (catches the linter-truncation class of bug).
4. `btcache.py` CANDIDATE target_rr 3.0 -> 2.0 (align tooling with LOCKED
   config; test_btcache.py updated).
5. NEW failure-alert wrapper in `brokebyte/screener/__main__.py`: any crash
   posts "⚠ BrokeByte screener FAILED: ..." to Telegram and re-raises
   (non-zero exit). NEW `tests/test_screener_main.py` (3 tests).
Suite: 349 pass locally (+ the 1 known sandbox-proxy fail that passes on real
machines).

## 1. Server steps (in order)
1. On Kris's machine: `del C:\Users\yello\Desktop\Trader\.git\index.lock`
   then commit + push the working tree (message suggestion:
   "Fix truncated fx_per_gbp (committed in c7e2e7c); harden scan; tripwire test").
2. On the droplet: `cd /root/Trader && git pull && venv/bin/python -m pytest -q`
   (expect 350 collected, all pass).
3. VERIFY the truncation is gone on the server:
   `tail -3 brokebyte/screener/yfinance_provider.py` (must end `return None`).
4. Check how long the screener was down:
   `journalctl -u brokebyte-screener --since 2026-06-28 | tail -50`
   (expect NameError tracebacks). Report the count of failed runs to Kris.
5. Dry-run: `venv/bin/python -m brokebyte.screener --no-send` (expect a digest,
   possibly "no qualifying setups today" — that string IS success).
6. Confirm the timer will fire: `systemctl list-timers | grep screener`.

## 2. Status recap (unchanged)
- Config LOCKED: hold-20d, target 2:1, stop 2% below swing low, break-even at
  +1R, 2% trail after +1.5R, index-200SMA regime gate ON, £5/trade, max 3.
- Validation: regime-faithful walk-forward ~+0.12R/trade after 10bps,
  positive 4/5 folds, n=123 — promising, not proof.
- ONLY remaining gate: 2-week paper demo (15-20 trades) vs the +0.12R
  benchmark, honoring circuit breakers (3-loss streak, index<200SMA).
  NOTE: demo clock effectively restarts when the screener is back up.

## 3. Housekeeping
- `git checkout -- requirements.txt` (recurring BOM/CRLF cosmetic diff).
- `tests/test_screener_pipeline.py` working-tree change (asserts the Manage
  plan lines in alerts) is legit — include it in the commit.
