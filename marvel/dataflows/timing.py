"""Per-source timing, so "slow" can be told apart from "wedged".

During the freeze investigation there was no way to answer the only question that
mattered: *which* fetch is the run sitting in? Every data source is a blocking HTTP
or TCP call, the pipeline is sequential, and nothing recorded how long any of them
took — so a run stuck for 15 minutes looked exactly like a run doing work.

This module is deliberately tiny and dependency-free: a bounded, thread-safe ring of
``(name, seconds, finished_at)`` samples that the Web UI can render, plus a log
warning when one call crosses a threshold. It is diagnostics, not control flow —
nothing here can raise into a data path, and nothing here changes a result.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)

#: How many samples to keep. Bounded so a long batch run cannot grow without limit.
_MAX_SAMPLES = 40

#: A single fetch slower than this gets a warning line (the call *did* finish).
SLOW_CALL_S = 5.0

#: A fetch still running after this long is worth a warning too — this is the one
#: that actually helps when something is wedged rather than merely slow.
STUCK_WARN_S = 60.0

_samples: deque[tuple[str, float, float]] = deque(maxlen=_MAX_SAMPLES)
_lock = threading.Lock()


def record(name: str, seconds: float, finished_at: float | None = None) -> None:
    """Remember one completed fetch."""
    with _lock:
        _samples.append((str(name), float(seconds), finished_at or time.time()))


@contextmanager
def timed(name: str, *, slow_over: float = SLOW_CALL_S) -> Iterator[None]:
    """Time a fetch, record it, and warn once if it was slow.

    Never swallows or raises: the wrapped call's own exceptions propagate untouched
    (the timing is recorded first, so a *failing* slow source is still visible).
    """
    started = time.time()
    try:
        yield
    finally:
        seconds = time.time() - started
        record(name, seconds)
        if seconds >= slow_over:
            logger.warning("数据源 %s 耗时 %.1fs（阈值 %.1fs）", name, seconds, slow_over)


def recent(limit: int = 8) -> list[tuple[str, float, float]]:
    """Most recent samples, newest last."""
    with _lock:
        return list(_samples)[-limit:]


def slowest(limit: int = 5) -> list[tuple[str, float, float]]:
    """The slowest samples currently held, slowest first."""
    with _lock:
        return sorted(_samples, key=lambda s: s[1], reverse=True)[:limit]


def reset() -> None:
    """Forget every sample (tests, and a fresh analysis)."""
    with _lock:
        _samples.clear()


def summary() -> str:
    """One-line human summary of the slowest recent sources, or '' if none."""
    items = slowest(3)
    if not items:
        return ""
    return " · ".join(f"{name} {seconds:.1f}s" for name, seconds, _ in items)
