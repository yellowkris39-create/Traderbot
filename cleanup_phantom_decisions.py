"""Void phantom ENTER decisions in decisions.db.

A "phantom" is a row logged as action='ENTER' whose bracket order was never
actually submitted to the broker: it has no broker_order_id and no recorded
outcome (pnl IS NULL).  These arose from the pre-fix ordering bug where an
ENTER decision was persisted *before* the market-hours / duplicate checks
could defer it.  Such rows have no Alpaca position and no exit fill, so the
reconciler logs `monitor_no_exit_order` for them on every pass and they inflate
the open-position count forever.

This script sets action='VOID' on those rows.  That removes them from BOTH:
  - the open-ENTER set  (DecisionStore.open_enter_decisions filters action='ENTER')
  - the closed-trade set (metrics filter pnl IS NOT NULL)
so they are neither reconciled nor counted as fake $0 trades.

Safe by design:
  - Targets ONLY action='ENTER' AND pnl IS NULL AND broker_order_id IS NULL.
    Real open positions all carry a broker_order_id, so they are untouched.
  - Dry-run by default; --apply is required to write.
  - On --apply it first copies the DB to a timestamped .bak file.

Usage (run on the server, from /root/Trader):
    venv/bin/python cleanup_phantom_decisions.py            # dry run (default)
    venv/bin/python cleanup_phantom_decisions.py --apply    # perform the update
    venv/bin/python cleanup_phantom_decisions.py --db logs/decisions.db --apply
"""

from __future__ import annotations

import argparse
import datetime
import shutil
import sqlite3
from collections import Counter

PHANTOM_WHERE = "action = 'ENTER' AND pnl IS NULL AND broker_order_id IS NULL"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="logs/decisions.db", help="path to decisions.db")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        f"SELECT id, recorded_at, verdict_symbol, plan_side, plan_qty "
        f"FROM decisions WHERE {PHANTOM_WHERE} ORDER BY id"
    ).fetchall()

    open_before = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE action = 'ENTER' AND pnl IS NULL"
    ).fetchone()[0]

    print(f"DB: {args.db}")
    print(f"Open ENTER decisions (action=ENTER, pnl NULL): {open_before}")
    print(f"Phantom rows to void (also broker_order_id NULL): {len(rows)}")
    for r in rows:
        print(
            f"  id={r['id']:<5} {(r['verdict_symbol'] or '<none>'):6} "
            f"{(r['plan_side'] or '?'):4} qty={r['plan_qty']} @ {r['recorded_at']}"
        )
    print("  by symbol:", dict(Counter(r["verdict_symbol"] for r in rows)))
    print(f"Open ENTER decisions remaining after void: {open_before - len(rows)}")

    if not rows:
        print("\nNothing to do.")
        return

    if not args.apply:
        print("\nDRY RUN — no changes written. Re-run with --apply to void these rows.")
        return

    backup = f"{args.db}.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(args.db, backup)
    print(f"\nBackup written: {backup}")

    note = f"voided phantom (never submitted) {datetime.date.today().isoformat()}"
    cur = conn.execute(
        f"UPDATE decisions SET action = 'VOID', "
        f"reason = COALESCE(reason, '') || ' | ' || ? WHERE {PHANTOM_WHERE}",
        (note,),
    )
    conn.commit()
    open_after = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE action = 'ENTER' AND pnl IS NULL"
    ).fetchone()[0]
    print(f"APPLIED: voided {cur.rowcount} rows. Open ENTER decisions now: {open_after}")


if __name__ == "__main__":
    main()
