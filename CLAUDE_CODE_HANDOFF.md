# Claude Code handoff — BrokeByte

Paste relevant parts to Claude Code (it has SSH to the droplet; Cowork doesn't).
All code below is unit-tested locally (303 tests pass; the 1 local "failure" is a
sandbox SOCKS-proxy artifact in the anthropic client — passes on the server).

## STATUS
- Exit manager VERIFIED on paper 2026-06-28: flatten closed NOK cleanly, zero
  orphan orders. get_current_price / get_open_stop / replace_stop / flatten all work.
- Screener built; needs a live yfinance dry-run (Yahoo unreachable from the build box).

## IMPORTANT FIX TO PULL (broker.py)
You fixed `get_order_exit_fill` on the server to use GetOrderByIdRequest. The SAME
call was wrong in `get_open_stop` AND `flatten` (alpaca-py 0.43: get_order_by_id
takes `filter=GetOrderByIdRequest(nested=True)`, not `nested=True`). Their try/except
masked it — meaning **break-even / trailing stop moves would silently never fire**
because get_open_stop always returned None. My local `broker.py` now fixes all three
call sites (module-level `_NESTED = GetOrderByIdRequest(nested=True)`). Make the
server's broker.py match this (pull my version), then confirm get_open_stop returns a
real (leg_id, stop_price) for an OPEN bracket — that's the proof break-even works.

## WHAT CHANGED SINCE LAST HANDOFF
- broker.py: all get_order_by_id calls use filter=_NESTED (fixes get_open_stop/flatten).
- exits.py: added the TRAILING stop (2% below price at >=1.5R, ratchets up only,
  never below entry). Time-stop + break-even already there.
- screener: FX added — yfinance_provider.fx_per_gbp(currency) via GBP{CCY}=X;
  Screener passes it into sizing so US ($) positions size correctly. If the rate
  is unavailable it falls back to 1.0 and the alert flags "apply FX".

## TASKS
1. `cd /root/Trader && git checkout -- requirements.txt`  (cosmetic BOM diff)
2. Pull/merge my broker.py + exits.py + screener changes; `git add -A && git commit`
   and push to the remote.
3. `venv/bin/python -m pytest -q`  (expect green).
4. Confirm break-even/trailing path: on an OPEN paper bracket, call
   `Broker(load_config()).get_open_stop("<bracket_order_id>")` — it must return
   (leg_id, stop_price), NOT None. (Pre-fix it returned None.)
5. yfinance dry-run + verify data:
   ```
   venv/bin/pip install yfinance
   venv/bin/python -m brokebyte.screener --no-send
   ```
   Check: LSE ('.L') prices in POUNDS not pence; US setups size correctly with the
   GBP/USD rate (no "apply FX" warning if the GBPUSD=X fetch worked).
6. Enable the daily timer:
   ```
   cp deploy/brokebyte-screener.{service,timer} /etc/systemd/system/
   systemctl daemon-reload && systemctl enable --now brokebyte-screener.timer
   systemctl start brokebyte-screener.service   # test webhook once
   ```

## REMAINING FOLLOW-UPS
- Backtest the merged ruleset over history; report win rate + avg R-multiple.
- Expand universe.py from the starter list to full S&P 500 + FTSE 350.
- (Optional) holiday-aware trading-day count for the time-stop (currently weekday count).
