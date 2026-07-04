"""Auto-executor: turns the nightly screener's qualifying setups into real
bracket orders on Alpaca paper at the next market open.

Flow (mirrors the validated backtest, which enters at the NEXT bar's open):
  21:30 UTC  screener scan -> qualifying US setups appended to
             logs/pending_signals.jsonl (write_pending, called by __main__)
  next open  this module (systemd timer, weekdays 13:35 + 14:35 UTC to cover
             DST) consumes the file and places whole-share bracket orders.

Sizing: VIRTUAL $10k account (Kris's 2026-07-04 decision) — 1% ($100) risk
per trade, 20% ($2k) exposure cap, max 3 open swing positions, whole shares
(Alpaca brackets don't allow fractional).

Discipline enforced in code, matching the locked config:
  * signals older than MAX_SIGNAL_AGE_H are discarded (backtest only ever
    entered the open AFTER the signal bar — stale signals are a different
    trade than the one validated);
  * 3 consecutive realized swing losses -> circuit breaker: no new entries
    (Telegram notice instead);
  * symbols already held (either strategy) are skipped;
  * a signal that gapped below its stop by the open is skipped (bracket
    would be instantly invalid).
Exits after entry are the bracket legs + brokebyte.monitor.exit_manager
(break-even +1R, 2% trail after +1.5R, 20-trading-day time-stop for
strategy='swing' rows).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from brokebyte.risk.sizing import PositionPlan

VIRTUAL_ACCOUNT_USD = 10_000.0
RISK_PCT = 0.01
MAX_POSITION_PCT = 0.20
REWARD_RISK = 2.0
MAX_OPEN_SWING = 3
BREAKER_LOSSES = 3
MAX_SIGNAL_AGE_H = 30.0


def pending_path() -> Path:
    return Path(os.environ.get("LOG_DIR", "logs")) / "pending_signals.jsonl"


def write_pending(results, path: Path | None = None, *, now: datetime | None = None) -> int:
    """Append the scan's qualifying US setups (LSE has no Alpaca listing).
    Returns how many were written. Called by brokebyte.screener.__main__."""
    path = path or pending_path()
    now = now or datetime.now(timezone.utc)
    us = [r for r in results if not r.symbol.upper().endswith(".L") and r.plan is not None]
    if not us:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for r in us:
            fh.write(json.dumps({"ts": now.isoformat(), "symbol": r.symbol, "signal_close": r.price, "stop": r.plan.stop_price}) + "\n")
    return len(us)


def load_pending(path: Path | None = None, *, now: datetime | None = None, max_age_hours: float = MAX_SIGNAL_AGE_H) -> tuple[list[dict], int]:
    """Read pending signals, dropping stale ones. Returns (fresh, n_stale)."""
    path = path or pending_path()
    now = now or datetime.now(timezone.utc)
    if not Path(path).exists():
        return [], 0
    fresh, stale = [], 0
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        sig = json.loads(line)
        age_h = (now - datetime.fromisoformat(sig["ts"])).total_seconds() / 3600.0
        if age_h > max_age_hours:
            stale += 1
        else:
            fresh.append(sig)
    return fresh, stale


def clear_pending(path: Path | None = None) -> None:
    path = path or pending_path()
    Path(path).unlink(missing_ok=True)


def size_swing(symbol: str, entry: float, stop: float, *, account: float = VIRTUAL_ACCOUNT_USD, risk_pct: float = RISK_PCT, max_position_pct: float = MAX_POSITION_PCT, reward_risk: float = REWARD_RISK) -> PositionPlan | None:
    """Whole-share long sizing for the virtual account; None if no valid size.
    Same two-cap logic as the validated GBP sizer (risk cap vs exposure cap,
    smaller wins), but integer shares for Alpaca bracket orders."""
    if entry <= 0 or stop <= 0 or stop >= entry:
        return None
    risk_per_share = entry - stop
    qty_by_risk = int((account * risk_pct) / risk_per_share)
    qty_by_exposure = int((account * max_position_pct) / entry)
    qty = min(qty_by_risk, qty_by_exposure)
    if qty <= 0:
        return None
    return PositionPlan(symbol=symbol, side="buy", qty=qty, entry_price=round(entry, 2), stop_price=round(stop, 2), take_profit_price=round(entry + reward_risk * risk_per_share, 2), risk_amount=round(qty * risk_per_share, 2), notional=round(qty * entry, 2))


@dataclass(frozen=True)
class ExecResult:
    executed: list
    skipped: list  # (symbol, reason)
    breaker_tripped: bool


def execute_pending(broker, store, signals: list[dict], log, *, now: datetime | None = None) -> ExecResult:
    """Place bracket orders for fresh signals, honoring every guard. Pure
    orchestration — broker/store are injectable fakes in tests."""
    now = now or datetime.now(timezone.utc)
    executed, skipped = [], []

    streak = store.consecutive_swing_losses()
    if streak >= BREAKER_LOSSES:
        log.warning("swing_breaker_tripped", streak=streak, signals_dropped=len(signals))
        return ExecResult([], [(s["symbol"], "circuit breaker: {} consecutive losses".format(streak)) for s in signals], True)

    held = broker.get_position_symbols()
    for sig in signals:
        symbol = sig["symbol"].upper()
        if store.open_swing_count() >= MAX_OPEN_SWING:
            skipped.append((symbol, "max {} open swing positions".format(MAX_OPEN_SWING)))
            continue
        if symbol in held:
            skipped.append((symbol, "symbol already held"))
            continue
        price = broker.get_current_price(symbol)
        if price is None:
            skipped.append((symbol, "no live price"))
            continue
        stop = float(sig["stop"])
        if price <= stop:
            skipped.append((symbol, "gapped below stop ({} <= {})".format(price, stop)))
            continue
        plan = size_swing(symbol, float(price), stop)
        if plan is None:
            skipped.append((symbol, "no valid whole-share size"))
            continue
        decision_id = store.record_swing_entry(symbol=symbol, side="buy", qty=plan.qty, entry_price=plan.entry_price, stop_price=plan.stop_price, take_profit_price=plan.take_profit_price, risk_amount=plan.risk_amount, notional=plan.notional, reason="screener signal {}".format(sig.get("ts", "?")[:10]))
        try:
            order = broker.submit_bracket_order(plan)
        except Exception as exc:  # noqa: BLE001
            store.mark_not_executed(decision_id, "swing order submission failed: {}".format(exc))
            log.error("swing_order_failed", symbol=symbol, error=str(exc))
            skipped.append((symbol, "order submission failed"))
            continue
        store.update_order_id(decision_id, str(order.id))
        held.add(symbol)
        log.info("swing_order_submitted", symbol=symbol, qty=plan.qty, entry=plan.entry_price, stop=plan.stop_price, target=plan.take_profit_price, order_id=str(order.id))
        executed.append(plan)

    return ExecResult(executed, skipped, False)


def format_execution_report(result: ExecResult, n_stale: int = 0) -> str:
    lines = ["BrokeByte swing executor"]
    if result.breaker_tripped:
        lines.append("*** CIRCUIT BREAKER: 3 consecutive losses — NO new entries until reviewed ***")
    if result.executed:
        lines.append("executed {}:".format(len(result.executed)))
        for p in result.executed:
            lines.append("  BUY {} x{} @ ~{:.2f}  stop {:.2f}  target {:.2f}  (risk ~${:.0f})".format(p.symbol, p.qty, p.entry_price, p.stop_price, p.take_profit_price, p.risk_amount))
    else:
        lines.append("no orders placed")
    for sym, reason in result.skipped:
        lines.append("  skipped {}: {}".format(sym, reason))
    if n_stale:
        lines.append("  ({} stale signal(s) discarded)".format(n_stale))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    dry = "--dry-run" in argv

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass

    from brokebyte.config import load_config
    from brokebyte.execution.broker import Broker
    from brokebyte.logging_setup import get_logger
    from brokebyte.memory.store import DecisionStore
    from brokebyte.screener import alerts

    log = get_logger("brokebyte.screener.executor")
    signals, n_stale = load_pending()
    if not signals and not n_stale:
        print("[executor] no pending signals")
        return

    broker = Broker(load_config())
    if not broker.is_market_open():
        print("[executor] market closed — leaving pending file for the next timer slot")
        return

    store = DecisionStore(os.environ.get("DECISIONS_DB", "logs/decisions.db"))
    if dry:
        print("[executor] DRY RUN — {} fresh signal(s), {} stale".format(len(signals), n_stale))
        return

    result = execute_pending(broker, store, signals, log)
    clear_pending()
    report = format_execution_report(result, n_stale)
    print(report)
    if result.executed or result.breaker_tripped:
        alerts.send(report)


if __name__ == "__main__":
    main()
