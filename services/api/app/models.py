from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class Activity(StrEnum):
    sleeping = "sleeping"
    resting = "resting"
    standing = "standing"
    walking = "walking"
    running = "running"
    playing = "playing"
    pacing = "pacing"
    looking_around = "looking_around"
    unknown = "unknown"


class BehavioralState(StrEnum):
    relaxed = "relaxed"
    engaged = "engaged"
    alert = "alert"
    stress_signals = "stress_signals"
    indeterminate = "indeterminate"


class MonitoringStatus(StrEnum):
    starting = "starting"
    analyzing = "analyzing"
    camera_offline = "camera_offline"
    stream_unstable = "stream_unstable"
    dog_not_visible = "dog_not_visible"
    multiple_dogs_detected = "multiple_dogs_detected"
    insufficient_visibility = "insufficient_visibility"
    service_degraded = "service_degraded"


class SessionStatus(StrEnum):
    active = "active"
    stopped = "stopped"


class IntegrationStatus(StrEnum):
    available = "available"
    degraded = "degraded"
    unavailable = "unavailable"
    not_configured = "not_configured"


class DogCreate(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    timezone: str = Field(default="America/Sao_Paulo", min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(value.strip().split())


class DogUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)


class Dog(StrictModel):
    id: str
    name: str
    timezone: str
    created_at: datetime
    updated_at: datetime


class CameraCreate(StrictModel):
    dog_id: str
    name: str = Field(min_length=1, max_length=100)
    rtsp_url: str = Field(min_length=8, max_length=2048)
    username: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=512)
    active: bool = True


class CameraUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    rtsp_url: str | None = Field(default=None, min_length=8, max_length=2048)
    username: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=512)
    active: bool | None = None


class Camera(StrictModel):
    id: str
    dog_id: str
    name: str
    active: bool
    source_type: Literal["rtsp", "rtsps"]
    rtsp_url_redacted: str
    has_credentials: bool
    encrypted_credentials: str
    status: MonitoringStatus = MonitoringStatus.starting
    last_checked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CameraPublic(StrictModel):
    id: str
    dog_id: str
    name: str
    active: bool
    source_type: Literal["rtsp", "rtsps"]
    rtsp_url_redacted: str
    has_credentials: bool
    status: MonitoringStatus
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, camera: Camera) -> CameraPublic:
        return cls(**camera.model_dump(exclude={"encrypted_credentials"}))


class CameraTestResult(StrictModel):
    success: bool
    camera_id: str
    frames_received: int = Field(ge=0)
    codec: str | None = None
    resolution: str | None = None
    fps: float | None = Field(default=None, ge=0)
    first_frame_ms: int | None = Field(default=None, ge=0)
    preview_available: bool = False
    message: str
    checked_at: datetime


class CameraHealth(StrictModel):
    camera_id: str
    status: MonitoringStatus
    online: bool
    last_checked_at: datetime | None


class SessionCreate(StrictModel):
    dog_id: str
    camera_id: str


class MonitoringSession(StrictModel):
    id: str
    dog_id: str
    camera_id: str
    status: SessionStatus
    started_at: datetime
    stopped_at: datetime | None = None


class Signal(StrictModel):
    name: str = Field(pattern=r"^[a-z0-9_\-]{1,64}$")
    confidence: float = Field(ge=0, le=1)


class ActivityValue(StrictModel):
    label: Activity
    confidence: float = Field(ge=0, le=1)


class StateValue(StrictModel):
    label: BehavioralState
    confidence: float = Field(ge=0, le=1)
    duration_seconds: int = Field(default=0, ge=0)
    started_at: datetime | None = None


class QualityValue(StrictModel):
    dog_visible: bool
    dogs_detected: int = Field(ge=0)
    observation_quality: float = Field(ge=0, le=1)
    body_visibility: float = Field(ge=0, le=1)
    face_visibility: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def visibility_consistency(self) -> QualityValue:
        if self.dogs_detected == 0 and self.dog_visible:
            raise ValueError("dog_visible must be false when dogs_detected is zero")
        return self


class AnalysisMetadata(StrictModel):
    schema_version: Literal["behavior-analysis-v1"]
    prompt_version: Literal["behavior-observer-v1"]
    model: str = Field(min_length=1, max_length=120)
    latency_ms: int = Field(ge=0, le=120_000)


class CurrentState(StrictModel):
    dog_id: str
    camera_id: str
    session_id: str
    monitoring_status: MonitoringStatus
    activity: ActivityValue | None
    state: StateValue | None
    signals: list[Signal] = Field(default_factory=list, max_length=5)
    quality: QualityValue | None
    analysis: AnalysisMetadata | None
    sequence: int = Field(ge=0)
    captured_at: datetime
    updated_at: datetime


class Transition(StrictModel):
    changed: bool
    previous_state: BehavioralState | None = None
    reason: str | None = Field(default=None, max_length=160)


class AnalysisIngest(StrictModel):
    schema_version: Literal["worker-ingest-v1"]
    analysis_id: UUID
    session_id: UUID
    camera_id: UUID
    captured_at: datetime
    transition_seq: int = Field(ge=0)
    monitoring_status: MonitoringStatus
    activity: ActivityValue | None
    state: StateValue | None
    signals: list[Signal] = Field(max_length=5)
    quality: QualityValue | None
    analysis: AnalysisMetadata | None
    transition: Transition

    @field_validator("captured_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def payload_matches_status(self) -> AnalysisIngest:
        if self.monitoring_status == MonitoringStatus.analyzing:
            fields = (self.activity, self.state, self.quality, self.analysis)
            if any(value is None for value in fields):
                raise ValueError("analyzing payloads require activity, state, quality and analysis")
            return self

        observation_statuses = {
            MonitoringStatus.dog_not_visible,
            MonitoringStatus.multiple_dogs_detected,
            MonitoringStatus.insufficient_visibility,
        }
        if self.monitoring_status in observation_statuses:
            if self.quality is None or self.analysis is None:
                raise ValueError("observation status payloads require quality and analysis")
            if self.activity is not None or self.state is not None or self.signals:
                raise ValueError("observation status payloads must not include activity, state or signals")
            return self

        fields = (self.activity, self.state, self.quality, self.analysis)
        if any(value is not None for value in fields):
            raise ValueError("technical status payloads must not fabricate inference fields")
        if self.signals:
            raise ValueError("technical status payloads require an empty signals list")
        return self


class StateEvent(StrictModel):
    id: str
    dog_id: str
    camera_id: str
    session_id: str
    started_at: datetime
    ended_at: datetime | None
    activity: Activity
    state: BehavioralState
    confidence_avg: float = Field(ge=0, le=1)
    confidence_max: float = Field(ge=0, le=1)
    observation_quality_avg: float = Field(ge=0, le=1)
    signals: list[Signal] = Field(max_length=5)
    prompt_version: str
    model_name: str
    source: str = "google_ai"
    snowflake_synced_at: datetime | None = None
    created_at: datetime
    sample_count: int = Field(default=1, ge=1)

    @property
    def duration_seconds(self) -> int:
        end = self.ended_at or utcnow()
        return max(0, int((end - self.started_at).total_seconds()))


class EventPublic(StrictModel):
    id: str
    dog_id: str
    camera_id: str
    session_id: str
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int = Field(ge=0)
    activity: Activity
    state: BehavioralState
    confidence_avg: float
    confidence_max: float
    observation_quality_avg: float
    signals: list[Signal]
    prompt_version: str
    model_name: str
    source: str
    snowflake_synced_at: datetime | None
    created_at: datetime

    @classmethod
    def from_record(cls, event: StateEvent) -> EventPublic:
        return cls(**event.model_dump(exclude={"sample_count"}), duration_seconds=event.duration_seconds)


class FeedbackCreate(StrictModel):
    correct: bool
    corrected_state: BehavioralState | None = None
    comment: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def correction_when_incorrect(self) -> FeedbackCreate:
        if not self.correct and self.corrected_state is None:
            raise ValueError("corrected_state is required when correct is false")
        return self


class Feedback(StrictModel):
    id: str
    event_id: str
    correct: bool
    corrected_state: BehavioralState | None
    comment: str | None
    created_at: datetime


class SpeechCreate(StrictModel):
    language: Literal["en", "pt-BR"] = "en"


class SpeechAsset(StrictModel):
    id: str
    event_id: str
    language: Literal["en", "pt-BR"]
    text: str
    voice_id: str
    model_id: str
    template_version: str = "speech-template-v1"
    status: Literal["pending", "ready", "failed"]
    mime_type: str = "audio/wav"
    expires_at: datetime
    created_at: datetime
    error: str | None = None


class Receipt(StrictModel):
    id: str
    event_id: str
    network: Literal["devnet"]
    canonical_version: Literal["dogsense-event-v1"]
    canonical_snapshot: dict[str, Any]
    event_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    memo: str
    transaction_signature: str | None
    status: Literal["pending", "confirmed", "failed"]
    confirmed_at: datetime | None
    verification_status: Literal["unverified", "verified", "mismatch", "pending"]
    created_at: datetime
    error: str | None = None


class ReceiptVerification(StrictModel):
    verified: bool
    local_hash: str
    on_chain_hash: str | None
    transaction_status: str
    network: Literal["devnet"]
    snapshot_matches_event: bool


class IntegrationReport(StrictModel):
    name: str
    mode: Literal["fake", "real"]
    status: IntegrationStatus
    configured: bool
    latency_ms: int | None = None
    detail: str | None = None


class DemoBootstrap(StrictModel):
    mode: Literal["demo"]
    dog_id: str
    camera_id: str
    session_id: str
    current_event_id: str | None
    api_token: str | None = None


class IngestResponse(StrictModel):
    accepted: bool
    duplicate: bool
    analysis_id: str
    sequence: int
    current_state: CurrentState
    event_id: str | None
