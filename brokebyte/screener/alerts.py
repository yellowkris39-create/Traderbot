"""Alert formatting + delivery for the screener.

Formatting is pure (testable). Delivery reuses the SAME webhook env vars as
health_report.py so no new config is needed:
    HEALTH_WEBHOOK_KIND = slack | discord | telegram
    HEALTH_WEBHOOK_URL (slack/discord)  OR  HEALTH_TELEGRAM_TOKEN + HEALTH_TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import os

from brokebyte.screener.screen import ScreenResult


def format_alert(result: ScreenResult, *, index: int | None = None) -> str:
    """Render one qualifying setup with the 8 required fields."""
    p = result.plan
    assert p is not None and result.price is not None
    head = f"{result.symbol}" if index is None else f"{index}. {result.symbol}"
    lines = [
        f"{head} @ {result.price:.2f}",
        "  Why it matches:",
        *[f"    - {r}" for r in result.reasons],
        f"  Entry:        {p.entry_price:.2f}",
        f"  Stop-loss:    {p.stop_price:.2f}  (risk/share {p.risk_per_share:.2f})",
        f"  Target (2:1): {p.take_profit_price:.2f}",
        f"  Position:     {p.shares:g} shares  (~{p.notional:.2f}, risk ~{p.risk_amount:.2f})"
        + ("  [exposure-capped]" if p.exposure_capped else ""),
        "  Manage (validated plan):",
        f"    - move stop to break-even ({p.entry_price:.2f}) at +1R ({p.entry_price + p.risk_per_share:.2f})",
        f"    - trail 2% below high after +1.5R ({p.entry_price + 1.5 * p.risk_per_share:.2f})",
        "    - exit by ~20 trading days if neither target nor stop is hit",
        "  Key risks:",
        *[f"    - {r}" for r in result.risks],
    ]
    return "\n".join(lines)


def format_digest(results: list[ScreenResult], *, header: str = "BrokeByte screener") -> str:
    """Combine all qualifying setups into one message (or a 'no setups' note)."""
    if not results:
        return f"{header}: no qualifying setups today."
    blocks = [format_alert(r, index=i + 1) for i, r in enumerate(results)]
    body = "\n\n".join(blocks)
    return (
        f"{header}: {len(results)} setup(s)\n"
        "(ideas only — not advice; verify earnings/FX before trading)\n\n"
        + body
    )


def send(message: str) -> None:
    """Deliver via the configured webhook (mirrors health_report.send)."""
    kind = os.environ.get("HEALTH_WEBHOOK_KIND", "").strip().lower()
    try:
        import requests
    except Exception:  # noqa: BLE001
        requests = None
    if not kind or requests is None:
        print("[screener] no webhook sent (HEALTH_WEBHOOK_KIND unset or requests missing)")
        return
    try:
        if kind in ("slack", "discord"):
            url = os.environ["HEALTH_WEBHOOK_URL"]
            payload = {"text": message} if kind == "slack" else {"content": message}
            r = requests.post(url, json=payload, timeout=15)
            print(f"[screener] {kind} POST -> {r.status_code}")
        elif kind == "telegram":
            token = os.environ["HEALTH_TELEGRAM_TOKEN"]
            chat = os.environ["HEALTH_TELEGRAM_CHAT_ID"]
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": message},
                timeout=15,
            )
            print(f"[screener] telegram POST -> {r.status_code}")
        else:
            print(f"[screener] unknown HEALTH_WEBHOOK_KIND={kind!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"[screener] webhook send failed: {exc}")
