from __future__ import annotations

import html
import unicodedata
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

from .enums import ActivityLabel, SignalName, StateLabel

Score = Annotated[float, Field(strict=True, ge=0.0, le=1.0)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
Summary = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=300),
]
Limitation = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120),
]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Activity(StrictContract):
    label: ActivityLabel
    confidence: Score


class ProbableState(StrictContract):
    label: StateLabel
    confidence: Score


class StateScores(StrictContract):
    relaxed: Score
    engaged: Score
    alert: Score
    stress_signals: Score
    indeterminate: Score

    def as_dict(self) -> dict[StateLabel, float]:
        return {
            StateLabel.RELAXED: self.relaxed,
            StateLabel.ENGAGED: self.engaged,
            StateLabel.ALERT: self.alert,
            StateLabel.STRESS_SIGNALS: self.stress_signals,
            StateLabel.INDETERMINATE: self.indeterminate,
        }


class ObservedSignal(StrictContract):
    name: SignalName
    confidence: Score


class BehaviorAnalysis(StrictContract):
    """Strict representation of the `behavior-analysis-v1` model response."""

    schema_version: Literal["behavior-analysis-v1"]
    dog_visible: StrictBool
    dogs_detected: NonNegativeInt
    observation_quality: Score
    body_visibility: Score
    face_visibility: Score
    activity: Activity
    state: ProbableState
    state_scores: StateScores
    signals: Annotated[list[ObservedSignal], Field(max_length=5)]
    summary: Summary
    limitations: Annotated[list[Limitation], Field(max_length=5)]

    @model_validator(mode="after")
    def validate_visibility_and_signals(self) -> BehaviorAnalysis:
        if self.dogs_detected == 0 and self.dog_visible:
            raise ValueError("dog_visible must be false when dogs_detected is zero")
        if self.dog_visible and self.dogs_detected < 1:
            raise ValueError("a visible dog requires dogs_detected >= 1")
        names = [signal.name for signal in self.signals]
        if len(names) != len(set(names)):
            raise ValueError("signal names must be unique")
        return self


def sanitize_summary(value: str) -> str:
    """Return display-safe plain text without logging or retaining model markup."""

    without_controls = "".join(
        char
        for char in value.strip()
        if unicodedata.category(char) not in {"Cc", "Cf"} or char in {"\n", "\t"}
    )
    return html.escape(without_controls, quote=True)[:300]
