"""Module 7 — Calibration layer: hit-rate statistics by regime and
confidence bucket from closed ENTER decisions (SPEC.md Module 7 / Phase 5d).

Output is ADVISORY ONLY per SPEC.md Module 7: "parameter changes proposed
by the calibration layer are human-approved until late autonomy rungs."
compute_calibration() surfaces per-bucket stats for review via
memory/run_report.py; nothing is automatically changed in RiskLimits or
the gate's sizing logic.

MIN_CALIBRATION_SAMPLE gates each bucket -- stats for buckets below this
threshold should be treated as noise. sufficient_data is True only when at
least one bucket has enough closed trades to be meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass

from brokebyte.memory.store import DecisionStore

MIN_CALIBRATION_SAMPLE = 10

_CONFIDENCE_BUCKETS: list[tuple[float, float, str]] = [
    (0.0, 0.5, "0.0-0.5"),
    (0.5, 0.7, "0.5-0.7"),
    (0.7, 0.85, "0.7-0.85"),
    (0.85, 1.01, "0.85-1.0"),
]


@dataclass(frozen=True)
class BucketStats:
    count: int
    win_rate: float  # fraction of closed trades with pnl > 0
    mean_pnl: float  # average realized P&L per trade


@dataclass(frozen=True)
class CalibrationResult:
    by_regime: dict[str, BucketStats]  # keyed by regime_trend value ("up"/"down"/"choppy")
    by_confidence: dict[str, BucketStats]  # keyed by bucket label e.g. "0.7-0.85"
    sufficient_data: bool  # True if any bucket reaches MIN_CALIBRATION_SAMPLE


def _bucket_stats(rows: list) -> BucketStats:
    if not rows:
        return BucketStats(count=0, win_rate=0.0, mean_pnl=0.0)
    pnls = [r["pnl"] for r in rows]
    n = len(pnls)
    return BucketStats(
        count=n,
        win_rate=sum(1 for p in pnls if p > 0) / n,
        mean_pnl=sum(pnls) / n,
    )


def compute_calibration(store: DecisionStore) -> CalibrationResult:
    """Compute hit-rate statistics by regime and confidence bucket from all
    closed ENTER decisions in `store`."""
    rows = store.closed_enter_decisions()

    by_regime: dict[str, list] = {}
    by_confidence: dict[str, list] = {label: [] for _, _, label in _CONFIDENCE_BUCKETS}

    for row in rows:
        trend = row["regime_trend"] or "unknown"
        by_regime.setdefault(trend, []).append(row)

        conf = row["verdict_confidence"] or 0.0
        for lo, hi, label in _CONFIDENCE_BUCKETS:
            if lo <= conf < hi:
                by_confidence[label].append(row)
                break

    regime_stats = {trend: _bucket_stats(bucket_rows) for trend, bucket_rows in by_regime.items()}
    conf_stats = {label: _bucket_stats(bucket_rows) for label, bucket_rows in by_confidence.items()}

    sufficient = any(
        s.count >= MIN_CALIBRATION_SAMPLE
        for s in list(regime_stats.values()) + list(conf_stats.values())
    )

    return CalibrationResult(
        by_regime=regime_stats,
        by_confidence=conf_stats,
        sufficient_data=sufficient,
    )
