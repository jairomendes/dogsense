from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID


@dataclass(frozen=True, slots=True, repr=False)
class CapturedFrame:
    sequence: int
    monotonic_ts: float
    captured_at: datetime
    jpeg: bytes
    fingerprint: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("frame sequence must be non-negative")
        if self.monotonic_ts < 0:
            raise ValueError("monotonic timestamp must be non-negative")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        if not self.jpeg:
            raise ValueError("jpeg payload must not be empty")
        if self.width < 1 or self.height < 1:
            raise ValueError("frame dimensions must be positive")

    def __repr__(self) -> str:
        return (
            "CapturedFrame("
            f"sequence={self.sequence}, monotonic_ts={self.monotonic_ts!r}, "
            f"captured_at={self.captured_at.astimezone(UTC).isoformat()!r}, "
            f"jpeg=<redacted {len(self.jpeg)} bytes>, fingerprint={self.fingerprint}, "
            f"width={self.width}, height={self.height})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class AnalysisWindow:
    sequence: int
    analysis_id: UUID
    session_id: UUID
    camera_id: UUID
    frames: tuple[CapturedFrame, ...]
    captured_at: datetime

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("window sequence must be non-negative")
        if not self.frames:
            raise ValueError("an analysis window requires at least one frame")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")

    def __repr__(self) -> str:
        return (
            "AnalysisWindow("
            f"sequence={self.sequence}, analysis_id={self.analysis_id!s}, "
            f"session_id={self.session_id!s}, camera_id={self.camera_id!s}, "
            f"frame_count={len(self.frames)}, captured_at={self.captured_at.isoformat()!r})"
        )
