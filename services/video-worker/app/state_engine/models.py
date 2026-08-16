from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from app.contracts import (
    Activity,
    MonitoringStatus,
    ObservationQuality,
    ObservedSignal,
    StableState,
    StateLabel,
    StateTransition,
)


@dataclass(slots=True)
class StateEvent:
    state: StateLabel
    started_at: datetime
    event_id: UUID = field(default_factory=uuid4)
    ended_at: datetime | None = None
    confidence_sum: float = 0.0
    sample_count: int = 0
    alert_emitted: bool = False

    def observe(self, confidence: float) -> None:
        self.confidence_sum += confidence
        self.sample_count += 1

    @property
    def average_confidence(self) -> float:
        return self.confidence_sum / self.sample_count if self.sample_count else 0.0

    def close(self, at: datetime) -> None:
        if self.ended_at is None:
            self.ended_at = max(at, self.started_at)


@dataclass(frozen=True, slots=True)
class EngineSnapshot:
    monitoring_status: MonitoringStatus
    transition_seq: int
    activity: Activity | None
    state: StableState | None
    signals: tuple[ObservedSignal, ...]
    quality: ObservationQuality | None
    transition: StateTransition
    closed_event: StateEvent | None = None
    alert: bool = False
