from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from app.capture import AnalysisWindow, CapturedFrame
from app.contracts import BehaviorAnalysis

FIXTURES = Path(__file__).parent / "fixtures" / "google-ai"


def load_analysis(name: str) -> BehaviorAnalysis:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return BehaviorAnalysis.model_validate(raw)


@pytest.fixture
def relaxed() -> BehaviorAnalysis:
    return load_analysis("relaxed.json")


@pytest.fixture
def frame_factory():
    def factory(
        sequence: int,
        timestamp: float | None = None,
        *,
        fingerprint: int | None = None,
        jpeg: bytes | None = None,
    ) -> CapturedFrame:
        return CapturedFrame(
            sequence=sequence,
            monotonic_ts=float(sequence if timestamp is None else timestamp),
            captured_at=datetime.fromtimestamp(1_700_000_000 + sequence, UTC),
            jpeg=jpeg or f"jpeg-{sequence}".encode(),
            fingerprint=(0xF << (sequence * 4)) if fingerprint is None else fingerprint,
            width=640,
            height=360,
        )

    return factory


@pytest.fixture
def window_factory(frame_factory):
    def factory(sequence: int = 1, frame_count: int = 6) -> AnalysisWindow:
        frames = tuple(frame_factory(index, timestamp=index * 0.5) for index in range(frame_count))
        return AnalysisWindow(
            sequence=sequence,
            analysis_id=UUID(f"00000000-0000-0000-0000-{sequence:012d}"),
            session_id=UUID("00000000-0000-0000-0000-000000000010"),
            camera_id=UUID("00000000-0000-0000-0000-000000000020"),
            frames=frames,
            captured_at=frames[-1].captured_at,
        )

    return factory
