"""Discrete-event scheduler: the single source of simulated time.

Time is an integer number of simulated milliseconds. Events fire in strict
(time, insertion order) order, so a run is fully determined by its inputs.
The scheduler never looks at the wall clock.
"""
from __future__ import annotations

import heapq
from typing import Callable, List, Optional, Tuple


class Scheduler:
    def __init__(self, on_error: Optional[Callable[[Exception], None]] = None):
        self.now_ms = 0
        self._heap: List[Tuple[int, int, Callable[[], None]]] = []
        self._seq = 0
        self._on_error = on_error

    def schedule(self, delay_ms: int, fn: Callable[[], None]) -> None:
        self.schedule_at(self.now_ms + max(0, int(delay_ms)), fn)

    def schedule_at(self, time_ms: int, fn: Callable[[], None]) -> None:
        self._seq += 1
        heapq.heappush(self._heap, (max(time_ms, self.now_ms), self._seq, fn))

    def run_until(self, target_ms: int) -> int:
        """Run every event due at or before ``target_ms``; returns events run."""
        ran = 0
        while self._heap and self._heap[0][0] <= target_ms:
            time_ms, _, fn = heapq.heappop(self._heap)
            self.now_ms = time_ms
            try:
                fn()
            except Exception as exc:  # keep the simulation alive, surface the bug
                if self._on_error is None:
                    raise
                self._on_error(exc)
            ran += 1
        self.now_ms = max(self.now_ms, target_ms)
        return ran

    @property
    def pending(self) -> int:
        return len(self._heap)
