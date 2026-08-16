from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.capture.frame import CapturedFrame

from .deduplication import deduplicate_frames


@dataclass(frozen=True, slots=True)
class TemporalSampler:
    window_seconds: float = 4.0
    sample_fps: float = 2.0
    min_frames: int = 6
    max_frames: int = 8
    duplicate_hamming_distance: int = 3

    def __post_init__(self) -> None:
        if self.window_seconds <= 0 or self.sample_fps <= 0:
            raise ValueError("window_seconds and sample_fps must be positive")
        if self.min_frames < 1 or self.max_frames < self.min_frames:
            raise ValueError("frame limits are inconsistent")
        if self.duplicate_hamming_distance < 0:
            raise ValueError("duplicate distance must be non-negative")

    def select(self, frames: Sequence[CapturedFrame]) -> list[CapturedFrame]:
        if not frames:
            return []
        ordered = sorted(frames, key=lambda frame: frame.monotonic_ts)
        latest = ordered[-1].monotonic_ts
        lower_bound = latest - self.window_seconds
        in_window = [frame for frame in ordered if frame.monotonic_ts >= lower_bound]
        temporally_sampled = self._sample_at_target_rate(in_window)
        unique = deduplicate_frames(
            temporally_sampled,
            max_hamming_distance=self.duplicate_hamming_distance,
        )
        if len(unique) > self.max_frames:
            unique = self._evenly_spaced(unique, self.max_frames)
        if len(unique) < self.min_frames:
            return []
        return unique

    def _sample_at_target_rate(self, frames: Sequence[CapturedFrame]) -> list[CapturedFrame]:
        if not frames:
            return []
        minimum_gap = 1.0 / self.sample_fps
        selected = [frames[0]]
        for frame in frames[1:]:
            if frame.monotonic_ts - selected[-1].monotonic_ts + 1e-9 >= minimum_gap:
                selected.append(frame)
        if selected[-1] is not frames[-1]:
            # Prefer freshness; replace a too-close final sample instead of exceeding target FPS.
            if frames[-1].monotonic_ts - selected[-1].monotonic_ts < minimum_gap:
                selected[-1] = frames[-1]
            else:
                selected.append(frames[-1])
        return selected

    @staticmethod
    def _evenly_spaced(frames: Sequence[CapturedFrame], count: int) -> list[CapturedFrame]:
        if count == 1:
            return [frames[-1]]
        last_index = len(frames) - 1
        indices = [round(index * last_index / (count - 1)) for index in range(count)]
        return [frames[index] for index in indices]
