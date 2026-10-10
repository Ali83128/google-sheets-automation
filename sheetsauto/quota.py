"""Stay under the Sheets API write quota.

Google Sheets allows about 60 write requests per minute per user. ``BatchWriter``
groups row updates into a few ``batch_update`` calls, spaces requests out, and
retries 429 responses with exponential backoff.
"""
from __future__ import annotations

import random
import time
from typing import Callable, Sequence, TypeVar

from .backend import RangeUpdate, RateLimitError, SheetsBackend

T = TypeVar("T")


def with_backoff(
    fn: Callable[[], T],
    *,
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 64.0,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    log: Callable[[str], None] | None = None,
) -> T:
    """Call ``fn``; on RateLimitError wait base*2^n (+jitter, capped) and retry."""
    attempt = 0
    while True:
        try:
            return fn()
        except RateLimitError:
            if attempt >= max_retries:
                raise
            delay = min(max_delay, base_delay * (2**attempt)) + jitter()
            if log:
                log(f"429 quota hit, retry {attempt + 1}/{max_retries} in {delay:.1f}s")
            sleep(delay)
            attempt += 1


def merge_contiguous(updates: Sequence[RangeUpdate]) -> list[RangeUpdate]:
    """Join updates on consecutive rows of the same tab into one range."""
    merged: list[RangeUpdate] = []
    for u in sorted(updates, key=lambda x: (x.tab, x.start_row)):
        last = merged[-1] if merged else None
        if last and last.tab == u.tab and last.start_row + len(last.rows) == u.start_row:
            last.rows.extend(u.rows)
        else:
            merged.append(RangeUpdate(u.tab, u.start_row, [list(r) for r in u.rows]))
    return merged


class BatchWriter:
    def __init__(
        self,
        backend: SheetsBackend,
        *,
        max_ranges_per_request: int = 50,
        requests_per_minute: int = 50,
        max_retries: int = 5,
        base_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
        log: Callable[[str], None] | None = None,
    ):
        self.backend = backend
        self.max_ranges = max_ranges_per_request
        self.min_interval = 60.0 / requests_per_minute if requests_per_minute else 0.0
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.sleep = sleep
        self.clock = clock
        self.jitter = jitter
        self.log = log
        self._pending: list[RangeUpdate] = []
        self._last_request: float | None = None
        self.requests_sent = 0

    def add(self, update: RangeUpdate) -> None:
        self._pending.append(update)

    def _throttle(self) -> None:
        if self._last_request is not None and self.min_interval:
            wait = self.min_interval - (self.clock() - self._last_request)
            if wait > 0:
                self.sleep(wait)
        self._last_request = self.clock()

    def call(self, fn: Callable[[], T]) -> T:
        """Run one API request with throttling and backoff."""

        def attempt() -> T:
            self._throttle()
            self.requests_sent += 1
            return fn()

        return with_backoff(
            attempt,
            max_retries=self.max_retries,
            base_delay=self.base_delay,
            sleep=self.sleep,
            jitter=self.jitter,
            log=self.log,
        )

    def flush(self) -> int:
        """Send pending updates. Returns the number of batch requests made."""
        merged = merge_contiguous(self._pending)
        self._pending = []
        sent = 0
        for i in range(0, len(merged), self.max_ranges):
            chunk = merged[i : i + self.max_ranges]
            self.call(lambda c=chunk: self.backend.batch_update(c))
            sent += 1
        return sent
