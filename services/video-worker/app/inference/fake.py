from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.capture.frame import AnalysisWindow
from app.contracts.behavior import BehaviorAnalysis

from .base import InferenceError, InferenceResult, InferenceTimeout

DEFAULT_ANALYSIS: dict[str, Any] = {
    "schema_version": "behavior-analysis-v1",
    "dog_visible": True,
    "dogs_detected": 1,
    "observation_quality": 0.9,
    "body_visibility": 0.9,
    "face_visibility": 0.5,
    "activity": {"label": "resting", "confidence": 0.9},
    "state": {"label": "relaxed", "confidence": 0.84},
    "state_scores": {
        "relaxed": 0.84,
        "engaged": 0.18,
        "alert": 0.08,
        "stress_signals": 0.03,
        "indeterminate": 0.05,
    },
    "signals": [
        {"name": "low_motion", "confidence": 0.9},
        {"name": "loose_body_posture", "confidence": 0.82},
    ],
    "summary": "The dog is resting with low movement and a loose body posture.",
    "limitations": ["Face partially visible"],
}


@dataclass(frozen=True, slots=True)
class FakeScenarioStep:
    at_seconds: float
    analysis: BehaviorAnalysis | None = None
    error: str | None = None
    latency_ms: int = 0


class DeterministicFakeAdapter:
    """Clock-driven fake used by CI and the controlled demonstration."""

    model = "fake-deterministic"

    def __init__(
        self,
        steps: list[FakeScenarioStep] | None = None,
        *,
        loop: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        default = FakeScenarioStep(
            at_seconds=0.0,
            analysis=BehaviorAnalysis.model_validate(DEFAULT_ANALYSIS),
        )
        self.steps = sorted(steps or [default], key=lambda step: step.at_seconds)
        if not self.steps or self.steps[0].at_seconds < 0:
            raise ValueError("a fake scenario requires non-negative steps")
        self.loop = loop
        self._clock = clock
        self._started_at: float | None = None
        self.calls = 0

    @classmethod
    def from_files(
        cls,
        *,
        fixture_path: Path | None = None,
        scenario_path: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> DeterministicFakeAdapter:
        if fixture_path and scenario_path:
            raise ValueError("configure either a fake fixture or a fake scenario, not both")
        if fixture_path:
            payload = cls._read_json(fixture_path)
            try:
                analysis = BehaviorAnalysis.model_validate(payload)
            except ValidationError as exc:
                raise ValueError("fake fixture does not match behavior-analysis-v1") from exc
            return cls([FakeScenarioStep(0.0, analysis=analysis)], clock=clock)
        if scenario_path:
            payload = cls._read_json(scenario_path)
            return cls._from_scenario_payload(payload, clock=clock)
        return cls(clock=clock)

    @classmethod
    def _from_scenario_payload(
        cls,
        payload: Any,
        *,
        clock: Callable[[], float],
    ) -> DeterministicFakeAdapter:
        if not isinstance(payload, dict) or set(payload) - {"loop", "steps", "schema_version"}:
            raise ValueError("fake scenario has unknown fields")
        if payload.get("schema_version", "fake-scenario-v1") != "fake-scenario-v1":
            raise ValueError("unsupported fake scenario version")
        if not isinstance(payload.get("loop", False), bool):
            raise ValueError("fake scenario loop must be boolean")
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("fake scenario must contain steps")
        steps: list[FakeScenarioStep] = []
        for raw in raw_steps:
            if not isinstance(raw, dict) or set(raw) - {
                "at_seconds",
                "analysis",
                "error",
                "latency_ms",
            }:
                raise ValueError("fake scenario step has unknown fields")
            at_seconds = raw.get("at_seconds")
            latency_ms = raw.get("latency_ms", 0)
            error = raw.get("error")
            if isinstance(at_seconds, bool) or not isinstance(at_seconds, (int, float)):
                raise ValueError("at_seconds must be numeric")
            if isinstance(latency_ms, bool) or not isinstance(latency_ms, int) or latency_ms < 0:
                raise ValueError("latency_ms must be a non-negative integer")
            if error not in {None, "timeout", "unavailable"}:
                raise ValueError("unsupported fake error")
            raw_analysis = raw.get("analysis")
            if (raw_analysis is None) == (error is None):
                raise ValueError("each fake step needs exactly one of analysis or error")
            try:
                analysis = (
                    BehaviorAnalysis.model_validate(raw_analysis)
                    if raw_analysis is not None
                    else None
                )
            except ValidationError as exc:
                raise ValueError("fake scenario analysis is invalid") from exc
            steps.append(
                FakeScenarioStep(
                    at_seconds=float(at_seconds),
                    analysis=analysis,
                    error=error,
                    latency_ms=latency_ms,
                )
            )
        return cls(steps, loop=payload.get("loop", False) is True, clock=clock)

    async def analyze(self, window: AnalysisWindow) -> InferenceResult:
        del window  # The fake is deterministic and never inspects private image bytes.
        now = self._clock()
        if self._started_at is None:
            self._started_at = now
        elapsed = max(0.0, now - self._started_at)
        duration = self.steps[-1].at_seconds
        if self.loop and duration > 0:
            elapsed %= duration
        step = self.steps[0]
        for candidate in self.steps:
            if candidate.at_seconds <= elapsed:
                step = candidate
            else:
                break
        self.calls += 1
        if step.latency_ms:
            await asyncio.sleep(step.latency_ms / 1000)
        if step.error == "timeout":
            raise InferenceTimeout("deterministic fake timeout")
        if step.error == "unavailable":
            raise InferenceError("deterministic fake unavailable")
        assert step.analysis is not None
        return InferenceResult(
            analysis=step.analysis.model_copy(deep=True),
            model=self.model,
            latency_ms=step.latency_ms,
        )

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"unable to load fake data from {path}") from exc
