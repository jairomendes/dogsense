from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from .behavior import Activity, ObservedSignal, Score, StrictContract
from .enums import MonitoringStatus, StateLabel, TransitionReason


class StableState(StrictContract):
    label: StateLabel
    confidence: Score
    duration_seconds: Annotated[int, Field(strict=True, ge=0)]
    started_at: datetime

    @field_validator("started_at")
    @classmethod
    def require_utc_started_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("started_at must include a timezone")
        return value.astimezone(UTC)


class ObservationQuality(StrictContract):
    dog_visible: bool
    dogs_detected: Annotated[int, Field(strict=True, ge=0)]
    observation_quality: Score
    body_visibility: Score
    face_visibility: Score


class AnalysisMetadata(StrictContract):
    schema_version: Literal["behavior-analysis-v1"] = "behavior-analysis-v1"
    prompt_version: Literal["behavior-observer-v1"] = "behavior-observer-v1"
    model: Annotated[
        str,
        StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120),
    ]
    latency_ms: Annotated[int, Field(strict=True, ge=0)]


class StateTransition(StrictContract):
    changed: bool
    previous_state: StateLabel | None
    reason: TransitionReason


class WorkerIngest(StrictContract):
    schema_version: Literal["worker-ingest-v1"] = "worker-ingest-v1"
    analysis_id: UUID
    session_id: UUID
    camera_id: UUID
    captured_at: datetime
    transition_seq: Annotated[int, Field(strict=True, ge=0)]
    monitoring_status: MonitoringStatus
    activity: Activity | None
    state: StableState | None
    signals: Annotated[list[ObservedSignal], Field(max_length=5)]
    quality: ObservationQuality | None
    analysis: AnalysisMetadata | None
    transition: StateTransition

    @field_validator("captured_at")
    @classmethod
    def require_utc_captured_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        return value.astimezone(UTC)
