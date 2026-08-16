from __future__ import annotations

import pytest

from app.capture import FrameRingBuffer
from app.sampling import (
    LatestOnlyQueue,
    TemporalSampler,
    deduplicate_frames,
    hamming_distance,
)


def test_capture_hash_preserves_chromatic_motion() -> None:
    import cv2
    import numpy as np

    from app.capture import OpenCVFrameSource

    image = np.zeros((8, 9, 3), dtype=np.uint8)
    image[:, :, 2] = np.arange(9, dtype=np.uint8) * 20
    fingerprint = OpenCVFrameSource._difference_hash(image, cv2)
    assert fingerprint.bit_length() > 128


def test_ring_buffer_is_bounded_and_counts_capacity_drops(frame_factory) -> None:
    buffer = FrameRingBuffer(3)
    for index in range(5):
        assert buffer.append(frame_factory(index))
    assert [frame.sequence for frame in buffer] == [2, 3, 4]
    assert buffer.dropped_capacity == 2
    assert len(buffer) == 3


def test_ring_buffer_rejects_out_of_order_and_selects_recent(frame_factory) -> None:
    buffer = FrameRingBuffer(10)
    assert buffer.append(frame_factory(1, 1.0))
    assert not buffer.append(frame_factory(2, 1.0))
    assert buffer.dropped_out_of_order == 1
    for index in range(2, 7):
        buffer.append(frame_factory(index, float(index)))
    assert [frame.monotonic_ts for frame in buffer.recent(2.0)] == [4.0, 5.0, 6.0]
    assert buffer.recent(2.0, now=100.0) == []


def test_deduplication_retains_newest_copy(frame_factory) -> None:
    frames = [
        frame_factory(1, fingerprint=0),
        frame_factory(2, fingerprint=1),
        frame_factory(3, fingerprint=0xFFFF),
    ]
    unique = deduplicate_frames(frames, max_hamming_distance=1)
    assert [frame.sequence for frame in unique] == [2, 3]
    assert hamming_distance(0, 0b1111) == 4


def test_sampler_returns_six_to_eight_evenly_spaced_recent_frames(frame_factory) -> None:
    frames = [frame_factory(index, timestamp=index * 0.25) for index in range(21)]
    sampler = TemporalSampler()
    selected = sampler.select(frames)
    assert len(selected) == 8
    assert selected[-1].monotonic_ts == frames[-1].monotonic_ts
    assert selected[0].monotonic_ts >= frames[-1].monotonic_ts - 4.0
    assert all(
        left.monotonic_ts < right.monotonic_ts
        for left, right in zip(selected, selected[1:], strict=False)
    )


def test_sampler_refuses_short_or_frozen_sequences(frame_factory) -> None:
    sampler = TemporalSampler()
    assert sampler.select([frame_factory(index) for index in range(5)]) == []
    frozen = [frame_factory(index, timestamp=index * 0.5, fingerprint=123) for index in range(9)]
    assert sampler.select(frozen) == []


@pytest.mark.asyncio
async def test_latest_only_queue_replaces_pending_window() -> None:
    queue: LatestOnlyQueue[int] = LatestOnlyQueue()
    await queue.put_latest(1)
    await queue.put_latest(2)
    await queue.put_latest(3)
    assert queue.qsize == 1
    assert queue.dropped == 2
    assert await queue.get() == 3
    queue.task_done()


def test_frame_repr_never_contains_image_bytes(frame_factory) -> None:
    frame = frame_factory(1, jpeg=b"private-home-frame-canary")
    rendered = repr(frame)
    assert "private-home-frame-canary" not in rendered
    assert "<redacted" in rendered
