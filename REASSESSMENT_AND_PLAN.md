# BrokeByte — Reassessment & Redesign Plan

_Date: 2026-06-28 · Status: PLAN (for your approval before any code changes)_

This document does two things you asked for: (1) reassess what we've built and why the bot
isn't working, and (2) lay out how to change the strategy, data, and platform. Per your
choices: the new strategy runs as a **broker-agnostic screener that sends alerts** (no live
order automation), it runs **in parallel** with the existing news bot, and you want this
**plan first** before I touch code.

A note on confidence: claims about the current code below are based on reading the files this
session. Claims about third-party data sources and broker APIs are flagged where I'm not
certain — please verify those before we commit to them.

---

## 1. Why the bot "never closes trades" (root cause)

The headline symptom — paper equity flat at ~$99.8k, **0 closed trades** — is not one bug.
It's the combination of an entry path that almost never fires and an exit path that has no
backstop.

**Exit side — the decisive cause.** Positions are entered as Alpaca *bracket orders*, so the
only ways a trade can ever close are (a) its stop child fills or (b) its take-profit child
fills. `reconcile_open_positions()` in `brokebyte/monitor/reconcile.py` only books an outcome
when `broker.get_order_exit_fill(order_id)` finds a *filled* child order. There is **no
time-based exit, no break-even move, no trailing stop, and no manual flatten**. So any position
that drifts sideways between its stop and target sits open indefinitely, and the reconciler
correctly reports "nothing to close" forever. With a handful of low-volatility names
(the memory notes EOSE/HR/NBIS/NOK/SBRA), that's exactly what you'd expect: equity barely
moves and the closed-trade count stays at zero.

**Entry side — why so few positions exist in the first place.** `_process_event()` in
`brokebyte/main.py` filters hard, and several filters silently kill most candidates:

- **Market-hours deferral** (main.py ~line 251): if news arrives while the US market is
  closed, the entry is converted to HOLD and *never re-queued*. A large share of market-moving
  news breaks pre-market, after-hours, or overnight — those signals are simply dropped.
- **Non-US symbol rejection** (`_is_us_ticker`, ~line 200): anything exchange-prefixed
  (e.g. `LSE:BARC`) is dropped. Correct for an Alpaca-only data API, but it throws away a lot
  of the news stream.
- **LLM gating**: materiality + `is_already_priced_in` + a confidence floor
  (`RISK_MIN_CONFIDENCE`, default 0.60) must all pass.
- **Duplicate-symbol block**: one open position per symbol at a time.

The net effect is a system that rarely enters and, once in, has no mechanism to ever get out.
The June fixes (phantom-ENTER cleanup, order-based reconciliation) were correct and necessary,
but they addressed *bookkeeping*, not these two structural gaps.

**Conclusion:** even before changing strategy, the current engine needs a **time-stop / exit
manager** and a re-think of the entry gating. The new screener-based approach below sidesteps
both problems because *you* execute and manage exits, but I recommend we also patch the exit
manager into the news bot so it stops accumulating dead positions.

---

## 2. Reassessment of past decisions

What was sound and worth keeping:

- **Two-tier LLM (Haiku filter → Sonnet verdict)** is a sensible cost design and stays as the
  engine for the *news bot* track.
- **Risk-gate-before-execution** ordering, the SQLite decision log (`memory/store.py`), the
  backtest harness (`brokebyte/backtest/`), and the Telegram health report are all reusable
  infrastructure. We do not rebuild these.
- **ATR-based volatility sizing** (`risk/sizing.py`) is methodologically fine.

What should change:

- **Single-source, US-only data** via Alpaca. It can't see LSE stocks, has no earnings
  calendar, and `get_daily_bars()` only pulls ~100 calendar days — **not enough for a 200-day
  SMA** and it currently **drops the `volume` column**, which the new rules need. This is the
  biggest data limitation for the new strategy.
- **News-reactive only.** The new strategy is *mechanical and scheduled* (screen the universe
  once a day after the close), not event-driven. Different trigger model.
- **No exit manager.** Covered above.
- **No git remote** — the repo is local-only on the droplet (per project memory). One bad disk
  and the work is gone. We should add an off-machine backup early.

---

## 3. The new strategy — one reconciled ruleset

You gave two strategies. They overlap but conflict in places; running both verbatim would
contradict itself (e.g. "strong momentum vs market" vs. "RSI 40–60, not overbought"). Below is
a single canonical ruleset that merges them, with conflicts resolved. **Please sanity-check the
resolutions — these are my calls, not yours.**

### Universe & liquidity filters (run first)

- Listed on LSE, NYSE, or NASDAQ, and available as fractional shares.
- Price between £5–£200 (or $5–$200).
- Market cap > £500M (no penny stocks: price < £0.50 or cap < £200M).
- Average daily volume > 1,000,000 shares. _(Resolution: I took the stricter of the two —
  Strategy 1 said 1M, Strategy 2 said 500k. Using 1M for safer liquidity. Tell me if you'd
  rather use 500k to widen the net.)_
- Beta < 1.5.
- No earnings announcement within the next 7 days.

### Trend filters (the uptrend must be intact)

- Price above the 50-day SMA.
- 50-day SMA above 200-day SMA.
- 20-day EMA above 50-day SMA.

### Setup — the pullback

- Price has pulled back ~3–10% from a recent swing high, **to or just below the 20-day EMA**.
- RSI(14) between 40 and 60 at the pullback. _(Resolution of the momentum conflict: Strategy 1
  wants strong relative strength; Strategy 2 caps RSI at 60. These reconcile if we measure RSI
  at the **pullback low** (naturally mid-range) while requiring longer-horizon relative
  strength separately — see next bullet.)_
- Relative strength: the stock has outperformed its index (SPY for US, FTSE 100 for UK) over
  the last ~3 months. This satisfies Strategy 1's "strong momentum vs the broader market"
  without violating the RSI cap.

### Entry trigger (all must occur on the daily timeframe)

1. A bullish reversal candle forms at the pullback (hammer, bullish engulfing, or morning star).
2. Signal-day volume ≥ 20% above the 10-day average volume.
3. RSI(14) crosses back above 40.

### Risk, sizing & exits (per trade)

- Account: £500. Risk exactly **1% = £5** per trade.
- Stop-loss: 2% below the pullback's swing low.
- Position size: `shares = £5 / (entry − stop)`, rounded **down** to a fractional share.
- Cap: never more than 20% of the account (£100) in one position; **max 3 open positions**.
- Take-profit: **2:1** reward:risk (`entry + 2 × (entry − stop)`).
- Management: move stop to break-even at +1R; optional 2% trailing stop once +1.5R; **hard
  time-stop: exit by the close of trading day 10** if neither target nor stop has hit.

### Portfolio-level circuit breakers

- If the S&P 500 (US) or FTSE 100 (UK) closes below its own 200-day SMA → no new entries; flag
  open positions for exit ("go to cash" regime).
- After 3 consecutive losing trades (−£15) → pause new alerts for one week.

> **Scale note:** this is a £500 framework. The existing Alpaca paper account is ~$99.8k. The
> screener will compute share counts against a configurable account size, so it can report the
> £500 plan regardless of what the paper broker holds.

---

## 4. Data & platform changes (the hard part)

A broker-agnostic screener needs data the current stack doesn't have: LSE coverage,
200-day history with volume, market cap, beta, and an earnings calendar.

**Market data + fundamentals.** Alpaca alone can't do this. Options, with honest trade-offs:

- **`yfinance` (free).** Covers US + LSE (`.L` tickers), gives OHLCV history, market cap, beta,
  and earnings dates. _Caveat I want to flag: it's an unofficial scraper of Yahoo data — it
  breaks without warning, rate-limits, and LSE prices come in **pence (GBp)**, which is a
  common bug source. Good enough to prototype; not something I'd call production-grade. Please
  treat it as "verify before trusting."_
- **Financial Modeling Prep / Finnhub / EOD Historical (paid tiers).** More reliable,
  proper earnings calendars and fundamentals, official APIs. Cost money; free tiers are
  rate-limited. _I'm not 100% certain of current pricing/limits — verify on their sites._
- **Keep Alpaca** for US intraday/quotes if we ever automate the US side, but not as the
  screener's primary source.

My recommendation: **prototype on `yfinance`**, design the data layer behind a small interface
so we can swap in a paid provider later without touching strategy code. I'd like your call on
whether to start free or pay for reliability up front (question below).

**Universe source.** We need a ticker list to screen. Simplest robust approach: a curated,
version-controlled list (e.g. S&P 500 + FTSE 350 constituents) refreshed periodically, rather
than scraping a live screener. I can generate the starting lists.

**Alert delivery.** Reuse the existing Telegram webhook plumbing from `health_report.py`. Each
evening the screener posts the day's qualifying setups with all 8 fields you specified (ticker,
price, why it matches, entry, stop, target, position size, key risks). No orders are placed.

**Scheduling.** Run the screener once daily after both markets close (a systemd timer, same
pattern as `brokebyte-health.timer`), or on demand. I can also build a Cowork artifact / live
page so you can pull the latest scan any time.

---

## 5. Proposed architecture (parallel tracks)

```
brokebyte/
  (existing news bot — unchanged, gains only an exit/time-stop manager)
  screener/                  ← NEW, self-contained, broker-agnostic
    universe.py              curated LSE/US ticker lists + refresh
    data.py                  provider interface (yfinance impl first)
    indicators_ext.py        EMA, RSI, avg-volume, relative strength,
                             candlestick patterns  (extends analysis/indicators.py)
    rules.py                 the §3 ruleset as pure, unit-tested functions
    sizing_gbp.py            £500 / 1% / swing-low sizing (fractional shares)
    screen.py                orchestrates: universe → filters → setups → alerts
    alerts.py                formats + sends Telegram alerts (reuses webhook code)
    journal.py               logs every alert/idea to SQLite (reuses memory/store patterns)
  backtest/                  reused to validate the new rules historically
```

**Reuse map:** `analysis/indicators.py` (SMA/ATR), `memory/store.py` (decision/idea log),
`backtest/` (rule validation), `health_report.py` Telegram code, and the config/logging
scaffolding all carry over. **New code** is mainly the screener package and the candlestick /
RSI / relative-strength indicators that don't exist yet.

This keeps the two strategies fully separated: the news bot keeps trading the Alpaca paper
account; the screener never places an order — it only reports. They share infrastructure, not
logic.

---

## 6. Phased roadmap

_Progress (2026-06-28): Phases 1–5 are built and unit-tested locally (296 tests pass). Live verification of broker exits + yfinance data is pending Monday's open. Phase 6 (backtest validation) is next._

1. **Foundation & safety.** Add a git remote (off-machine backup). Patch a time-stop/exit
   manager into the news bot so it stops accumulating dead positions. _(Fixes the "never
   closes" bug independently of the new strategy.)_
2. **Indicators.** Add EMA, RSI(14), average-volume, relative-strength, and the three
   candlestick patterns to a new `indicators_ext.py`, each with unit tests.
3. **Data layer.** Build the provider interface + `yfinance` implementation; handle GBp→GBP
   conversion and 200-day+volume history. Generate starting universe lists.
4. **Rules + sizing.** Implement §3 as pure functions and the £500/1% swing-low sizer, all
   unit-tested against the worked example you provided (3.33 shares, £53 target, £5 risk).
5. **Orchestration + alerts.** Wire universe → filters → setups → Telegram alert with your 8
   fields. Add the daily systemd timer.
6. **Validation.** Backtest the merged ruleset over historical data, report win rate and
   average R-multiple, and dry-run the live screener for a couple of weeks before you trade it
   for real (matches Strategy 2's "demo for 2 weeks" rule).
7. **Optional:** add the LLM as a final news-risk sanity check on each screener candidate
   (your "mechanical + AI filter" idea, if you want it later).

---

## 7. Decisions — LOCKED (2026-06-28)

1. **Data source:** ✅ Build on **`yfinance` behind a provider interface**, swap to a paid
   source (Finnhub/FMP/EOD) later if it proves unreliable.
2. **Liquidity threshold:** ✅ **1,000,000** average daily shares (stricter).
3. **Markets:** ✅ **Both LSE and US** — adds FX + GBp/£ handling, covered with explicit tests.
4. **Account size for sizing:** ✅ Report the **£500 plan** by default; a paper-account-scaled
   version can be added later on request.
5. **Exit-manager patch:** ✅ **Yes — fix the news bot's time-stop in Phase 1**, alongside the
   new screener foundation.

---

## 8. Risks & honest caveats

- This is **not financial advice** and a screener that passes every rule is still a *candidate*,
  not a guarantee. Backtested edge ≠ live edge.
- `yfinance` and free data sources can be wrong or stale; earnings dates especially. Any "no
  earnings within 7 days" check is only as good as the calendar behind it — **verify before
  trading**.
- LSE pence/pounds and US/UK FX are real sources of silent sizing errors; I'll add explicit
  tests, but flag it here.
- A £500 account with fractional shares and 1% risk means some valid setups will size to a tiny
  position; that's expected and fine.
- I could not verify in this session that Trading 212 / Freetrade lack a public trading API —
  that's my understanding, not a confirmed fact. It's the reason we're going alerts-only; if
  one of them *does* expose an order API you trust, automation becomes possible later.
```
```

_Nothing in this plan has been implemented yet. Tell me your answers to §7 (or just say "go")
and I'll start with Phase 1._
