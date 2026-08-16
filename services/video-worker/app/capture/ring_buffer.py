from __future__ import annotations

from collections import deque
from collections.abc import Iterator

from .frame import CapturedFrame


class FrameRingBuffer:
    """A monotonic, memory-only frame buffer with an explicit upper bound."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._frames: deque[CapturedFrame] = deque(maxlen=capacity)
        self.capacity = capacity
        self.received = 0
        self.dropped_capacity = 0
        self.dropped_out_of_order = 0

    def append(self, frame: CapturedFrame) -> bool:
        self.received += 1
        if self._frames and frame.monotonic_ts <= self._frames[-1].monotonic_ts:
            self.dropped_out_of_order += 1
            return False
        if len(self._frames) == self.capacity:
            self.dropped_capacity += 1
        self._frames.append(frame)
        return True

    def recent(self, window_seconds: float, *, now: float | None = None) -> list[CapturedFrame]:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if not self._frames:
            return []
        reference = self._frames[-1].monotonic_ts if now is None else now
        lower_bound = reference - window_seconds
        return [frame for frame in self._frames if lower_bound <= frame.monotonic_ts <= reference]

    def clear(self) -> None:
        self._frames.clear()

    @property
    def latest(self) -> CapturedFrame | None:
        return self._frames[-1] if self._frames else None

    def __len__(self) -> int:
        return len(self._frames)

    def __iter__(self) -> Iterator[CapturedFrame]:
        return iter(tuple(self._frames))
