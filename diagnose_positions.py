"""Position-drift diagnostic for BrokeByte.

Compares what the bot THINKS it holds (open ENTER decisions in decisions.db,
i.e. action='ENTER' AND pnl IS NULL) against what Alpaca ACTUALLY shows
(live positions + recent order history).

Read-only: places no orders, cancels nothing, records no outcomes.

Run on the server:
    cd /root/Trader && venv/bin/python diagnose_positions.py
"""

from __future__ import annotations

from collections import Counter

from alpaca.common.enums import Sort
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from brokebyte.config import load_config
from brokebyte.execution.broker import Broker
from brokebyte.memory.store import DecisionStore


def main() -> None:
    config = load_config()
    broker = Broker(config)
    store = DecisionStore(config.log_dir / "decisions.db")

    print(f"=== ACCOUNT ({'paper' if config.is_paper else 'LIVE'}) ===")
    acct = broker.get_account_summary()
    for k in ("status", "equity", "last_equity", "cash", "buying_power", "shorting_enabled"):
        print(f"  {k}: {acct.get(k)}")

    # --- Live Alpaca positions ---
    live_positions = broker.get_positions()
    live_symbols = {p["symbol"] for p in live_positions}
    print(f"\n=== LIVE ALPACA POSITIONS ({len(live_positions)}) ===")
    for p in sorted(live_positions, key=lambda x: x["symbol"]):
        print(f"  {p['symbol']:6s} qty={p['qty']:>6} mkt_val={p['market_value']}")

    # --- DB open ENTER decisions ---
    open_rows = store.open_enter_decisions()
    db_symbol_counts = Counter(r["verdict_symbol"] for r in open_rows)
    print(f"\n=== DB OPEN ENTER DECISIONS ({len(open_rows)} rows, {len(db_symbol_counts)} symbols) ===")
    for r in open_rows:
        print(
            f"  id={r['id']:<4} {r['verdict_symbol'] or '<none>':6s} "
            f"side={r['plan_side'] or '?':4s} qty={r['plan_qty']} "
            f"entry={r['plan_entry_price']} order_id={r['broker_order_id'] or 'NULL'} "
            f"@ {r['recorded_at']}"
        )

    # --- Duplicates within the DB open set ---
    dupes = {s: n for s, n in db_symbol_counts.items() if n > 1}
    print(f"\n=== DUPLICATE OPEN DECISIONS (same symbol, >1 open row) ===")
    print(f"  {dupes if dupes else 'none'}")

    # --- Drift diff ---
    db_symbols = set(db_symbol_counts)
    phantom = db_symbols - live_symbols   # DB thinks open, Alpaca has no position
    untracked = live_symbols - db_symbols # Alpaca holds, no open DB decision
    print(f"\n=== DRIFT ===")
    print(f"  STUCK / PHANTOM (in DB-open, NOT in Alpaca, will log monitor_no_exit_order): {sorted(phantom)}")
    print(f"  UNTRACKED (in Alpaca, NOT in DB-open): {sorted(untracked)}")
    print(f"  MATCHED (in both): {sorted(db_symbols & live_symbols)}")

    # --- For each phantom symbol, show its order history so we can tell
    #     whether it ever filled at the broker at all ---
    print(f"\n=== ORDER HISTORY FOR PHANTOM SYMBOLS ===")
    for sym in sorted(phantom):
        req = GetOrdersRequest(
            status=QueryOrderStatus.ALL,
            symbols=[sym],
            direction=Sort.DESC,
            limit=50,
            nested=True,
        )
        orders = broker._client.get_orders(filter=req)  # read-only
        print(f"\n  --- {sym}: {len(orders)} order(s) ---")
        for o in orders:
            print(
                f"    {str(o.submitted_at)[:19]} {str(o.side):9} {str(o.order_class):16} "
                f"status={str(o.status):20} qty={o.qty} filled_avg={o.filled_avg_price}"
            )
            for leg in (o.legs or []):
                print(
                    f"        leg {str(leg.side):9} {str(leg.type):11} "
                    f"status={str(leg.status):20} filled_avg={leg.filled_avg_price}"
                )


if __name__ == "__main__":
    main()
