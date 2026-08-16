from __future__ import annotations

from collections.abc import Sequence

from app.capture.frame import CapturedFrame


def hamming_distance(left: int, right: int) -> int:
    if left < 0 or right < 0:
        raise ValueError("fingerprints must be non-negative")
    return (left ^ right).bit_count()


def is_near_duplicate(
    left: CapturedFrame,
    right: CapturedFrame,
    *,
    max_hamming_distance: int = 3,
) -> bool:
    if max_hamming_distance < 0:
        raise ValueError("max_hamming_distance must be non-negative")
    return hamming_distance(left.fingerprint, right.fingerprint) <= max_hamming_distance


def deduplicate_frames(
    frames: Sequence[CapturedFrame],
    *,
    max_hamming_distance: int = 3,
) -> list[CapturedFrame]:
    """Collapse consecutive near-identical images while retaining the newest copy."""

    unique: list[CapturedFrame] = []
    for frame in frames:
        if unique and is_near_duplicate(
            unique[-1], frame, max_hamming_distance=max_hamming_distance
        ):
            unique[-1] = frame
        else:
            unique.append(frame)
    return unique
