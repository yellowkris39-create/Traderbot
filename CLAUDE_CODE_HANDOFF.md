# Claude Code handoff — BrokeByte (exit-manager fix + screener)

Paste the block below to Claude Code (it has SSH to the droplet; Cowork doesn't).
Everything here was built and unit-tested locally (296 tests pass; the 1 local
"failure" is a sandbox SOCKS-proxy artifact in the anthropic client — it passes
on the server). Two things genuinely need the live environment and could NOT be
tested locally: (a) the broker exit methods against Alpaca, (b) yfinance data
(Yahoo is unreachable from the build sandbox).

See `REASSESSMENT_AND_PLAN.md` for the full design.

---

## WHAT CHANGED

Phase 1 — exit manager (fixes "0 closed trades"):
- NEW `brokebyte/monitor/exits.py`, `brokebyte/monitor/exit_manager.py`
- CHANGED `brokebyte/execution/broker.py` (+get_current_price/get_open_stop/replace_stop/flatten)
- CHANGED `brokebyte/main.py` (calls manage_open_positions() each reconcile cycle)

Phases 3–5 — broker-agnostic screener (alerts only, never trades):
- NEW `brokebyte/analysis/indicators_ext.py` (EMA, RSI, vol, rel-strength, candlesticks)
- NEW `brokebyte/screener/` (rules.py, sizing_gbp.py, data.py, yfinance_provider.py,
  screen.py, alerts.py, universe.py, __main__.py)
- NEW `deploy/brokebyte-screener.service` + `.timer` (daily 21:30 UTC)
- NEW tests: test_phase1_screener.py, test_exit_manager.py, test_screener_pipeline.py

## TASKS (in order)

1. `cd /root/Trader && git checkout -- requirements.txt`  (drops a cosmetic BOM-only diff)

2. Add an off-machine git remote and commit/push (repo is local-only — no backup):
   ```
   cd /root/Trader
   git add -A
   git commit -m "Phase 8: exit manager + broker-agnostic screener"
   git remote add origin <PRIVATE_REPO_URL>
   git push -u origin master   # or main
   ```

3. Run the suite on the server: `cd /root/Trader && venv/bin/python -m pytest -q`
   (expect all green).

4. **VERIFY broker exit methods on the PAPER account** (names verified, live
   semantics not). On one throwaway paper position, confirm: get_open_stop finds
   the STOP leg with a stop_price; replace_stop moves that leg (not the parent);
   flatten cancels open legs then close_position cleanly closes with a correct
   fill and no orphan orders. Only then restart:
   ```
   systemctl restart brokebyte && journalctl -u brokebyte -f
   ```
   Watch for exit_manage_cycle / exit_move_breakeven / exit_time_stop_closed.
   NOTE: existing paper positions older than 10 trading days will be time-stopped
   on the next cycle once flatten is verified — confirm that's desired first.

5. **Install yfinance + verify data**, then the screener:
   ```
   cd /root/Trader && venv/bin/pip install yfinance
   venv/bin/python -m brokebyte.screener --no-send        # dry run, prints only
   ```
   Sanity-check the printed setups: LSE ('.L') prices must be in POUNDS not pence
   (the provider divides GBp by 100 — confirm a known LSE price looks right), and
   for US names note the FX warning in "Key risks" (position size assumes £1=1
   unit until a live GBP/USD rate is wired — Phase 6 work).

6. Enable the daily screener timer (after step 5 looks right):
   ```
   cp deploy/brokebyte-screener.{service,timer} /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now brokebyte-screener.timer
   systemctl start brokebyte-screener.service   # one manual run to test the webhook
   ```
   Alerts reuse the existing HEALTH_WEBHOOK_* env (Telegram) — no new config.

Report back: server test result; whether the 4 broker methods behaved on paper
(any orphan orders from flatten?); and whether yfinance LSE prices came through
in pounds correctly.

## KNOWN FOLLOW-UPS (Phase 6, not yet built)
- Live GBP/USD FX so US-stock position sizes are correct (currently flagged, not converted).
- Backtest the merged ruleset over history; report win rate + avg R-multiple.
- Expand universe.py from the starter list to full S&P 500 + FTSE 350.
- 2% trailing stop at +1.5R (time-stop + break-even are done; trailing is not).
