"""High-resolution monotonic timer and statistical summary calculator."""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

import numpy as np


class HighResolutionTimer:
    """Monotonic timer recording latency samples with nanosecond precision."""

    def __init__(self, name: str = "operation") -> None:
        self.name = name
        self.samples_ms: list[float] = []

    def record(self, elapsed_ms: float) -> None:
        """Manually append an elapsed duration in milliseconds."""
        self.samples_ms.append(float(elapsed_ms))

    @contextmanager
    def measure(self) -> Generator[None, None, None]:
        """Context manager to measure the execution block in milliseconds."""
        start_ns = time.perf_counter_ns()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
            self.samples_ms.append(elapsed_ms)

    def time_call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[Any, float]:
        """Execute a callable, record its latency, and return (result, elapsed_ms)."""
        start_ns = time.perf_counter_ns()
        res = func(*args, **kwargs)
        elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
        self.samples_ms.append(elapsed_ms)
        return res, elapsed_ms

    def clear(self) -> None:
        """Reset all recorded samples."""
        self.samples_ms.clear()

    @property
    def sample_count(self) -> int:
        return len(self.samples_ms)

    def get_summary(self) -> dict[str, Any]:
        """Compute statistical summary dictionary.

        Note: P95 is reported as 'N/A' if sample count < 20 to preserve statistical integrity.
        """
        n = len(self.samples_ms)
        if n == 0:
            return {
                "name": self.name,
                "sample_count": 0,
                "min_ms": None,
                "median_ms": None,
                "mean_ms": None,
                "p95_ms": "N/A",
                "max_ms": None,
                "p95_note": "Sample size < 20; percentile not statistically meaningful.",
            }

        min_val = min(self.samples_ms)
        max_val = max(self.samples_ms)
        mean_val = statistics.mean(self.samples_ms)
        median_val = statistics.median(self.samples_ms)

        if n >= 20:
            p95_val = float(np.percentile(self.samples_ms, 95))
            p95_note = None
        else:
            p95_val = "N/A"
            p95_note = f"Sample size {n} < 20; percentile not statistically meaningful."

        return {
            "name": self.name,
            "sample_count": n,
            "min_ms": round(min_val, 4),
            "median_ms": round(median_val, 4),
            "mean_ms": round(mean_val, 4),
            "p95_ms": round(p95_val, 4) if isinstance(p95_val, (int, float)) else "N/A",
            "max_ms": round(max_val, 4),
            "p95_note": p95_note,
        }
