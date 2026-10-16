"""Structured logging configuration for MempoolGuard.

Formats log events with UTC ISO-8601 timestamps, log levels, logger names,
and optional contextual attributes in a clean, parseable format.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class UTCFormatter(logging.Formatter):
    """Logging formatter converting timestamps to ISO-8601 UTC."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()


class JSONFormatter(logging.Formatter):
    """JSON-based structured formatter for machine-readable logging."""

    def format(self, record: logging.LogRecord) -> str:
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        payload: dict[str, Any] = {
            "timestamp": dt.isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
            payload.update(record.extra_fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logger(
    name: str = "mempoolguard",
    level: str = "INFO",
    json_format: bool = False,
) -> logging.Logger:
    """Configure and return a structured logger instance."""
    logger = logging.getLogger(name)
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)

    # Avoid duplicate handlers if setup_logger is called repeatedly
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(numeric_level)
        if json_format:
            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(
                UTCFormatter(
                    fmt="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S.%fZ",
                )
            )
        logger.addHandler(handler)

    logger.propagate = False
    return logger
