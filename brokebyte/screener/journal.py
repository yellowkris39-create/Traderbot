"""Paper-trade journal + evaluator for the 2-week demo.

The screener emits IDEAS (logs/screener_alerts.jsonl); this journal tracks the
trades Kris actually TAKES on the broker demo and evaluates realized R against
the walk-forward benchmark (~+0.12R/trade after 10bps slippage).

    python -m brokebyte.screener.journal open AAPL 150.00 145.50
    python -m brokebyte.screener.journal open AAPL 150.00 145.50 --target=160
    python -m brokebyte.screener.journal close AAPL 159.00 --reason=target
    python -m brokebyte.screener.journal list
    python -m brokebyte.screener.journal report

Storage: logs/paper_trades.jsonl, one JSON object per trade, rewritten on
close (tiny file, simplicity beats cleverness here). All computation is in
pure functions so it unit-tests without I/O.

Discipline encoded, not just documented:
  * 3 consecutive losses -> CIRCUIT BREAKER banner: stop taking new alerts.
  * >3 open positions -> loud warning (plan says max 3).
  * n < 15 closed -> report refuses to compare against the benchmark
    (small-sample honesty; the demo bar is ~15-20 trades).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BENCHMARK_R = 0.12          # walk-forward expectancy after 10bps slippage
RISK_GBP = 5.0              # £5 risk per trade (1% of £500)
MAX_OPEN = 3
BREAKER_LOSSES = 3
MIN_SAMPLE = 15


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _journal_path() -> Path:
    return Path(os.environ.get("LOG_DIR", "logs")) / "paper_trades.jsonl"


def load_trades(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def save_trades(path: Path, trades: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(t) + "\n" for t in trades))


def realized_r(entry: float, stop: float, exit_price: float) -> float:
    """R-multiple of a closed long: (exit-entry)/(entry-stop). Requires stop<entry."""
    risk = entry - stop
    if risk <= 0:
        raise ValueError("stop must be below entry (long-only strategy)")
    return (exit_price - entry) / risk


def open_trade(trades: list[dict], symbol: str, entry: float, stop: float, target: float | None = None, date: str | None = None) -> dict:
    """Append a new open trade. Default target = entry + 2R (locked 2:1 config)."""
    symbol = symbol.upper()
    if entry <= 0 or stop <= 0 or stop >= entry:
        raise ValueError("need entry > stop > 0 (long-only)")
    date = date or _today()
    trade_id = "{}-{}".format(symbol, date.replace("-", ""))
    if any(t["id"] == trade_id for t in trades):
        raise ValueError("trade {} already exists".format(trade_id))
    risk = entry - stop
    trade = {
        "id": trade_id, "symbol": symbol, "status": "open", "opened": date,
        "entry": round(entry, 4), "stop": round(stop, 4),
        "target": round(target if target is not None else entry + 2.0 * risk, 4),
    }
    trades.append(trade)
    return trade


def close_trade(trades: list[dict], key: str, exit_price: float, reason: str = "manual", date: str | None = None) -> dict:
    """Close by full id, or by symbol when exactly one open trade matches."""
    key = key.upper()
    matches = [t for t in trades if t["status"] == "open" and (t["id"] == key or t["symbol"] == key)]
    if not matches:
        raise ValueError("no open trade matches {!r}".format(key))
    if len(matches) > 1:
        ids = ", ".join(t["id"] for t in matches)
        raise ValueError("ambiguous — close by id: {}".format(ids))
    t = matches[0]
    t["status"] = "closed"
    t["exit"] = round(exit_price, 4)
    t["exit_reason"] = reason
    t["closed"] = date or _today()
    t["r"] = round(realized_r(t["entry"], t["stop"], exit_price), 3)
    return t


def loss_streak(closed: list[dict]) -> int:
    """Consecutive losses (r < 0) at the END of the closed-trade sequence.
    A break-even close (r == 0) resets the streak."""
    streak = 0
    for t in sorted(closed, key=lambda t: (t.get("closed", ""), t["id"])):
        streak = streak + 1 if t["r"] < 0 else 0
    return streak


def evaluate(trades: list[dict]) -> dict:
    """Pure evaluator: stats + discipline flags for `report`."""
    open_ = [t for t in trades if t["status"] == "open"]
    closed = [t for t in trades if t["status"] == "closed"]
    rs = [t["r"] for t in closed]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    streak = loss_streak(closed)
    return {
        "open": len(open_), "closed": len(closed),
        "win_rate": (len(wins) / len(rs)) if rs else 0.0,
        "avg_r": (sum(rs) / len(rs)) if rs else 0.0,
        "total_r": sum(rs),
        "avg_win_r": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss_r": (sum(losses) / len(losses)) if losses else 0.0,
        "pnl_gbp": sum(rs) * RISK_GBP,
        "loss_streak": streak,
        "breaker_tripped": streak >= BREAKER_LOSSES,
        "over_max_open": len(open_) > MAX_OPEN,
        "sample_ok": len(closed) >= MIN_SAMPLE,
    }


def format_report(trades: list[dict]) -> str:
    e = evaluate(trades)
    lines = ["PAPER DEMO REPORT (benchmark {:+.2f}R/trade)".format(BENCHMARK_R)]
    if e["breaker_tripped"]:
        lines.append("*** CIRCUIT BREAKER: {} consecutive losses — STOP taking new alerts; review before resuming ***".format(e["loss_streak"]))
    if e["over_max_open"]:
        lines.append("*** WARNING: {} open positions exceeds the max of {} ***".format(e["open"], MAX_OPEN))
    lines.append("open {} (max {})   closed {}".format(e["open"], MAX_OPEN, e["closed"]))
    if e["closed"]:
        lines.append("win {:.1%}   avg {:+.3f}R   total {:+.2f}R   (~£{:+.2f} at £{:.0f}/trade)".format(e["win_rate"], e["avg_r"], e["total_r"], e["pnl_gbp"], RISK_GBP))
        lines.append("avg win {:+.2f}R / avg loss {:+.2f}R   current loss streak: {}".format(e["avg_win_r"], e["avg_loss_r"], e["loss_streak"]))
        if e["sample_ok"]:
            verdict = "AT/ABOVE benchmark" if e["avg_r"] >= BENCHMARK_R else "BELOW benchmark"
            lines.append("vs benchmark: {:+.3f}R vs {:+.2f}R -> {} (n={}; still a small sample — treat as evidence, not proof)".format(e["avg_r"], BENCHMARK_R, verdict, e["closed"]))
        else:
            lines.append("vs benchmark: n={} closed — too few to compare (need {}+); keep trading the plan".format(e["closed"], MIN_SAMPLE))
    else:
        lines.append("no closed trades yet — log outcomes with: journal close SYMBOL EXIT_PRICE --reason=target|stop|time")
    return "\n".join(lines)


def format_list(trades: list[dict]) -> str:
    open_ = [t for t in trades if t["status"] == "open"]
    if not open_:
        return "no open paper trades"
    lines = ["open paper trades ({}/{}):".format(len(open_), MAX_OPEN)]
    for t in sorted(open_, key=lambda t: t["opened"]):
        risk = t["entry"] - t["stop"]
        lines.append("  {}  entry {:.2f}  stop {:.2f}  target {:.2f}  (+1R={:.2f}, +1.5R={:.2f})  opened {}".format(t["id"], t["entry"], t["stop"], t["target"], t["entry"] + risk, t["entry"] + 1.5 * risk, t["opened"]))
    return "\n".join(lines)


def _kw(argv: list[str], name: str) -> str | None:
    for a in argv:
        if a.startswith("--" + name + "="):
            return a.split("=", 1)[1]
    return None


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    path = _journal_path()
    trades = load_trades(path)
    cmd = argv[0] if argv else "report"
    pos = [a for a in argv[1:] if not a.startswith("--")]

    if cmd == "open":
        if len(pos) < 3:
            raise SystemExit("usage: journal open SYMBOL ENTRY STOP [--target=X] [--date=YYYY-MM-DD]")
        target = _kw(argv, "target")
        t = open_trade(trades, pos[0], float(pos[1]), float(pos[2]), target=float(target) if target else None, date=_kw(argv, "date"))
        save_trades(path, trades)
        print("opened {}: entry {} stop {} target {}".format(t["id"], t["entry"], t["stop"], t["target"]))
        e = evaluate(trades)
        if e["breaker_tripped"]:
            print("*** CIRCUIT BREAKER is tripped — this entry violates the plan ***")
        if e["over_max_open"]:
            print("*** WARNING: now {} open positions (max {}) ***".format(e["open"], MAX_OPEN))
    elif cmd == "close":
        if len(pos) < 2:
            raise SystemExit("usage: journal close SYMBOL_OR_ID EXIT_PRICE [--reason=target|stop|time|manual] [--date=YYYY-MM-DD]")
        t = close_trade(trades, pos[0], float(pos[1]), reason=_kw(argv, "reason") or "manual", date=_kw(argv, "date"))
        save_trades(path, trades)
        print("closed {} at {} ({}) -> {:+.3f}R".format(t["id"], t["exit"], t["exit_reason"], t["r"]))
        print(format_report(trades))
    elif cmd == "list":
        print(format_list(trades))
    elif cmd == "report":
        print(format_report(trades))
    else:
        raise SystemExit("unknown command {!r} (open|close|list|report)".format(cmd))


if __name__ == "__main__":
    main()
