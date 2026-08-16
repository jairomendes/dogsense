from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.capture.frame import AnalysisWindow
from app.contracts.behavior import BehaviorAnalysis


class InferenceError(RuntimeError):
    """Provider failure that must not mutate behavioral state."""


class InferenceTimeout(InferenceError):
    pass


class InvalidModelResponse(InferenceError):
    pass


@dataclass(frozen=True, slots=True)
class InferenceResult:
    analysis: BehaviorAnalysis
    model: str
    latency_ms: int
    prompt_version: str = "behavior-observer-v1"
    schema_version: str = "behavior-analysis-v1"
    attempts: int = 1


class BehaviorAnalyzer(Protocol):
    async def analyze(self, window: AnalysisWindow) -> InferenceResult: ...
