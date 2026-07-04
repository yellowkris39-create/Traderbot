"""BrokeByte health report — runs on the server, posts to a chat webhook.

Designed to be invoked every few hours by a systemd timer (see
deploy/brokebyte-health.timer).  Gathers a concise snapshot and pushes it to
Slack, Discord, or Telegram depending on .env config.  Every section is
independently guarded, so a failure fetching one part (e.g. Alpaca hiccup)
still produces a useful report instead of crashing.

The report ALWAYS prints to stdout, so the systemd journal keeps a copy even
if the webhook is down or unconfigured.

Config (add to /root/Trader/.env):
    HEALTH_WEBHOOK_KIND=slack          # slack | discord | telegram
    HEALTH_WEBHOOK_URL=https://...      # Slack/Discord incoming webhook URL
    # for telegram instead:
    # HEALTH_WEBHOOK_KIND=telegram
    # HEALTH_TELEGRAM_TOKEN=123456:ABC...
    # HEALTH_TELEGRAM_CHAT_ID=987654321

Usage:
    cd /root/Trader && venv/bin/python health_report.py
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.environ.get("LOG_DIR", "logs") + "/decisions.db"
SERVICE = "brokebyte"


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"<error: {exc}>"


def gather_service() -> list[str]:
    out: list[str] = []
    try:
        props = _run(["systemctl", "show", SERVICE,
                      "-p", "ActiveState", "-p", "ActiveEnterTimestamp", "-p", "NRestarts"])
        kv = dict(line.split("=", 1) for line in props.splitlines() if "=" in line)
        state = kv.get("ActiveState", "unknown")
        since = kv.get("ActiveEnterTimestamp", "")
        restarts = kv.get("NRestarts", "?")
        flag = "OK" if state == "active" else "ALERT"
        out.append(f"[{flag}] service: {state} (since {since or '?'}, restarts={restarts})")
    except Exception as exc:  # noqa: BLE001
        out.append(f"[??] service: unavailable ({exc})")
    return out


def gather_account_positions() -> tuple[list[str], set[str]]:
    out: list[str] = []
    live_symbols: set[str] = set()
    try:
        from brokebyte.config import load_config
        from brokebyte.execution.broker import Broker

        broker = Broker(load_config())
        acct = broker.get_account_summary()
        equity = float(acct.get("equity") or 0)
        last_equity = float(acct.get("last_equity") or 0)
        day = equity - last_equity
        out.append(f"equity: ${equity:,.2f}  (day {day:+,.2f})  cash ${float(acct.get('cash') or 0):,.2f}")

        positions = broker.get_positions()
        live_symbols = {p["symbol"] for p in positions}
        out.append(f"live positions: {len(positions)}")
        for p in sorted(positions, key=lambda x: x["symbol"]):
            out.append(f"   {p['symbol']:6} qty={p['qty']} mv={p['market_value']}")
    except Exception as exc:  # noqa: BLE001
        out.append(f"[??] account/positions: unavailable ({exc})")
    return out, live_symbols


def gather_db(live_symbols: set[str]) -> list[str]:
    out: list[str] = []
    try:
        conn = sqlite3.connect(DB_PATH)
        total = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        enters = conn.execute("SELECT COUNT(*) FROM decisions WHERE action='ENTER'").fetchone()[0]
        open_enter = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE action='ENTER' AND pnl IS NULL"
        ).fetchone()[0]
        phantom = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE action='ENTER' AND pnl IS NULL AND broker_order_id IS NULL"
        ).fetchone()[0]
        closed = conn.execute("SELECT COUNT(*) FROM decisions WHERE pnl IS NOT NULL").fetchone()[0]
        realized = conn.execute("SELECT COALESCE(SUM(pnl),0) FROM decisions WHERE pnl IS NOT NULL").fetchone()[0]
        open_syms = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT verdict_symbol FROM decisions WHERE action='ENTER' AND pnl IS NULL"
            )
        }
        conn.close()

        out.append(f"decisions: {total} total, {enters} ENTER, {open_enter} open, {closed} closed")
        out.append(f"realized P&L (closed): ${realized:,.2f}")
        if phantom:
            out.append(f"[ALERT] phantom open ENTERs (no order id): {phantom} — run cleanup_phantom_decisions.py")
        if live_symbols:  # only meaningful if we got live positions
            stuck = sorted(open_syms - live_symbols)
            if stuck:
                out.append(f"[ALERT] DB-open but not in Alpaca: {stuck}")
    except Exception as exc:  # noqa: BLE001
        out.append(f"[??] decision DB: unavailable ({exc})")
    return out


def gather_logs() -> list[str]:
    out: list[str] = []
    try:
        since = "6 hours ago"
        logs = _run(["journalctl", "-u", SERVICE, "--since", since, "--no-pager"])
        errors = logs.count('"level": "error"')
        warns = logs.count('"level": "warning"')
        reconciles = [ln for ln in logs.splitlines() if "monitor_reconcile" in ln]
        last_recon = reconciles[-1] if reconciles else "(none in window)"
        flag = "ALERT" if errors else "OK"
        out.append(f"[{flag}] last 6h: {errors} errors, {warns} warnings")
        # trim the reconcile line to the useful bit
        if "monitor_reconcile" in last_recon:
            out.append("last reconcile: " + last_recon.split("]: ", 1)[-1][:160])
    except Exception as exc:  # noqa: BLE001
        out.append(f"[??] logs: unavailable ({exc})")
    return out


def build_report() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    acct_lines, live_symbols = gather_account_positions()
    lines = [f"BrokeByte health — {now}", ""]
    lines += gather_service()
    lines += acct_lines
    lines += gather_db(live_symbols)
    lines += gather_logs()
    report = "\n".join(lines)
    # respect the tightest webhook limit (Discord 2000 chars)
    return report[:1900]


def send(report: str) -> None:
    kind = os.environ.get("HEALTH_WEBHOOK_KIND", "").strip().lower()
    try:
        import requests
    except Exception:  # noqa: BLE001
        requests = None

    if not kind or requests is None:
        print("[health_report] no webhook sent (HEALTH_WEBHOOK_KIND unset or requests missing)")
        return
    try:
        if kind in ("slack", "discord"):
            url = os.environ["HEALTH_WEBHOOK_URL"]
            payload = {"text": report} if kind == "slack" else {"content": report}
            r = requests.post(url, json=payload, timeout=15)
            print(f"[health_report] {kind} POST -> {r.status_code}")
            if r.status_code != 200:
                print(f"[health_report] response body: {r.text[:500]}")
        elif kind == "telegram":
            token = os.environ["HEALTH_TELEGRAM_TOKEN"]
            chat = os.environ["HEALTH_TELEGRAM_CHAT_ID"]
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": report},
                timeout=15,
            )
            print(f"[health_report] telegram POST -> {r.status_code}")
            if r.status_code != 200:
                print(f"[health_report] response body: {r.text[:500]}")
        else:
            print(f"[health_report] unknown HEALTH_WEBHOOK_KIND={kind!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"[health_report] webhook send failed: {exc}")


def main() -> None:
    report = build_report()
    print(report)
    send(report)


if __name__ == "__main__":
    main()
