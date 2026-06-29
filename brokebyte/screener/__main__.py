"""Run a screener scan and deliver alerts.

    cd /root/Trader && venv/bin/python -m brokebyte.screener            # both markets
    venv/bin/python -m brokebyte.screener --us       # US only
    venv/bin/python -m brokebyte.screener --lse      # LSE only
    venv/bin/python -m brokebyte.screener --no-send  # print only, don't post

Intended to run once daily after both markets close (see deploy/
brokebyte-screener.timer). Reads webhook + account config from .env.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

from brokebyte.screener import alerts
from brokebyte.screener.screen import Screener
from brokebyte.screener.universe import starter_universe
from brokebyte.screener.yfinance_provider import YFinanceProvider


def _journal(results, log_dir: str) -> None:
    """Append each qualifying setup to logs/screener_alerts.jsonl (an audit log
    of every idea emitted, for later win-rate review)."""
    path = Path(log_dir) / "screener_alerts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with path.open("a") as fh:
        for r in results:
            p = r.plan
            fh.write(json.dumps({
                "ts": now, "symbol": r.symbol, "price": r.price,
                "entry": p.entry_price, "stop": p.stop_price,
                "target": p.take_profit_price, "shares": p.shares,
                "risk_amount": p.risk_amount, "reasons": r.reasons,
            }) + "\n")


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    include_us = "--lse" not in argv
    include_lse = "--us" not in argv
    do_send = "--no-send" not in argv

    account = float(os.environ.get("SCREENER_ACCOUNT_GBP", "500"))
    log_dir = os.environ.get("LOG_DIR", "logs")

    universe = starter_universe(include_us=include_us, include_lse=include_lse)
    screener = Screener(YFinanceProvider(), account=account)
    results = screener.scan(universe)

    message = alerts.format_digest(results)
    print(message)
    _journal(results, log_dir)
    if do_send:
        alerts.send(message)


if __name__ == "__main__":
    main()
