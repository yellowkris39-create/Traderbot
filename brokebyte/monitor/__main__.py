"""CLI entry point for the position-monitoring reconciliation loop.

Run with:
    venv\\Scripts\\python.exe -m brokebyte.monitor [DB_PATH]

Defaults to logs/decisions.db.  Connects to Alpaca, checks for closed
positions, records outcomes in DecisionStore, and prints a summary.

Schedule this with Task Scheduler / cron to run periodically during the
Track B soak.  It is idempotent: calling it when nothing has closed is a
no-op (0 outcomes recorded).
"""

from __future__ import annotations

import sys

from brokebyte.config import load_config
from brokebyte.execution.broker import Broker
from brokebyte.logging_setup import configure_logging, get_logger
from brokebyte.memory.store import DecisionStore
from brokebyte.monitor.reconcile import reconcile_open_positions


def main(db_path: str = "logs/decisions.db") -> None:
    config = load_config()
    configure_logging(config.log_dir)
    log = get_logger("brokebyte.monitor")

    store = DecisionStore(db_path)
    broker = Broker(config)

    open_count = len(store.open_enter_decisions())
    print(f"{db_path}: {open_count} open ENTER decision(s) to check")

    outcomes = reconcile_open_positions(broker, store, log)

    if not outcomes:
        print("No positions closed since last run.")
    else:
        print(f"{len(outcomes)} position(s) closed and recorded:")
        for o in outcomes:
            print(f"  decision {o.decision_id} | {o.symbol} | {o.exit_reason} | pnl=${o.pnl:+.2f}")


if __name__ == "__main__":
    cli_db_path = sys.argv[1] if len(sys.argv) > 1 else "logs/decisions.db"
    main(cli_db_path)
