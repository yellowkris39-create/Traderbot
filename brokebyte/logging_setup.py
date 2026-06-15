"""Structured (JSON) logging, to console and to a per-run log file.

Every pipeline stage logs through this so decisions — including rejected
ones — are reconstructable later (Module 6: Logging & Charting).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog


def configure_logging(log_dir: Path, level: int = logging.INFO) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_dir / "brokebyte.jsonl", encoding="utf-8"),
    ]
    for handler in handlers:
        handler.setFormatter(logging.Formatter("%(message)s"))

    logging.basicConfig(level=level, handlers=handlers, force=True)

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "brokebyte") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
