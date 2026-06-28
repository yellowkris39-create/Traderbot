# Claude Code handoff — BrokeByte Phase 1

Paste the block below to Claude Code (it has SSH to the droplet; Cowork doesn't).
Everything here was built and unit-tested locally (281 tests pass, 27 new). The
only things needing the live paper account are the git remote and verifying the
new broker exit methods against Alpaca.

---

## TASK FOR CLAUDE CODE

We pivoted BrokeByte: a new broker-agnostic technical screener is being added in
parallel with the news bot, and Phase 1 adds the missing exit manager that fixes
the "0 closed trades" bug. See `REASSESSMENT_AND_PLAN.md` for the full design.

New/changed files in the local repo (already present, tested):
- NEW `brokebyte/monitor/exits.py` — pure exit logic (time-stop day 10, break-even at +1R).
- NEW `brokebyte/monitor/exit_manager.py` — orchestrates exits via the broker.
- NEW `brokebyte/analysis/indicators_ext.py` — EMA, RSI, avg volume, relative strength, candlesticks.
- NEW `brokebyte/screener/` — package skeleton (rules.py, sizing_gbp.py, data.py, universe.py).
- NEW `tests/test_phase1_screener.py`, `tests/test_exit_manager.py` (27 tests).
- CHANGED `brokebyte/execution/broker.py` — added get_current_price, get_open_stop, replace_stop, flatten.
- CHANGED `brokebyte/main.py` — calls manage_open_positions() each reconcile cycle before reconcile_open_positions().

Please do the following, in order:

1. **Cosmetic cleanup:** `cd /root/Trader && git checkout -- requirements.txt`
   (a BOM/line-ending-only diff crept in; the package list is unchanged).

2. **Add an off-machine git remote and push** (the repo is currently local-only —
   no backup). Create a private remote (GitHub/GitLab), then:
   ```
   cd /root/Trader
   git add -A
   git commit -m "Phase 8: exit manager (time-stop + break-even) and screener foundation"
   git remote add origin <PRIVATE_REPO_URL>
   git push -u origin master   # or main
   ```

3. **Run the test suite on the server** to confirm parity:
   ```
   cd /root/Trader && venv/bin/python -m pytest -q
   ```
   Expect all green (the local sandbox's one failure was a proxy artifact, not a
   code issue — the server should pass it).

4. **VERIFY the new broker exit methods against the Alpaca PAPER account.** These
   wrap alpaca-py calls whose live behaviour must be confirmed (method *names* are
   verified, live *semantics* are not):
   - `get_open_stop(order_id)` — does the bracket's stop leg show `order_type ==
     STOP` and a populated `stop_price` while open? Confirm it's found.
   - `replace_stop(stop_leg_id, price)` — confirm `replace_order_by_id` on the
     STOP child leg actually moves the stop (not the parent order).
   - `flatten(symbol, order_id)` — confirm cancelling the open legs then
     `close_position(symbol)` cleanly closes without leaving orphan orders, and
     that the returned fill price is correct. **Test on one throwaway paper
     position first.**
   A safe check: open a tiny paper bracket, then in a Python shell call each
   method and inspect results before trusting the loop.

5. **Deploy and restart** once verified:
   ```
   systemctl restart brokebyte
   journalctl -u brokebyte -f
   ```
   Watch for `exit_manage_cycle`, `exit_move_breakeven`, and `exit_time_stop_closed`
   log lines on the reconcile interval.

6. **Optional immediate win:** there are existing open paper positions that may be
   past 10 trading days. After step 4 verifies `flatten`, the exit manager will
   time-stop them on the next cycle. Confirm that's desired before restart, or
   close them manually.

Report back: test result, whether the four broker methods behaved as expected on
paper, and any orphaned-order issues from `flatten`.
