from __future__ import annotations

from datetime import UTC, datetime

from app.contracts import (
    BehaviorAnalysis,
    MonitoringStatus,
    ObservationQuality,
    StableState,
    StateLabel,
    StateTransition,
    TransitionReason,
)

from .models import EngineSnapshot, StateEvent


class TemporalStateEngine:
    """EWMA stabilization and explicit technical-status state machine.

    Engine state is intentionally not restored after process restart. A new worker session
    starts without a behavioral event, so an event from a dead process cannot be silently
    extended across an observation gap.
    """

    def __init__(
        self,
        *,
        alpha: float = 0.35,
        transition_threshold: float = 0.65,
        transition_margin: float = 0.10,
        min_candidate_results: int = 2,
        min_observation_quality: float = 0.50,
        min_body_visibility: float = 0.50,
        indeterminate_max_confidence: float = 0.55,
        degradation_seconds: float = 10.0,
        absence_close_seconds: float = 10.0,
        stress_alert_confidence: float = 0.75,
        stress_alert_seconds: float = 10.0,
        initial_sequence: int = 0,
    ) -> None:
        if not 0 < alpha <= 1:
            raise ValueError("alpha must be in (0, 1]")
        if min_candidate_results < 1:
            raise ValueError("min_candidate_results must be positive")
        if initial_sequence < 0:
            raise ValueError("initial_sequence must be non-negative")
        self.alpha = alpha
        self.transition_threshold = transition_threshold
        self.transition_margin = transition_margin
        self.min_candidate_results = min_candidate_results
        self.min_observation_quality = min_observation_quality
        self.min_body_visibility = min_body_visibility
        self.indeterminate_max_confidence = indeterminate_max_confidence
        self.degradation_seconds = degradation_seconds
        self.absence_close_seconds = absence_close_seconds
        self.stress_alert_confidence = stress_alert_confidence
        self.stress_alert_seconds = stress_alert_seconds

        self.status = MonitoringStatus.STARTING
        self.smoothed_scores: dict[StateLabel, float] = {}
        self.stable_label: StateLabel | None = None
        self.stable_started_at: datetime | None = None
        self.pending_candidate: StateLabel | None = None
        self.pending_count = 0
        self.current_event: StateEvent | None = None
        self._last_activity = None
        self._last_signals = ()
        self._last_quality: ObservationQuality | None = None
        self._issue_key: str | None = None
        self._issue_started_monotonic: float | None = None
        self._failure_started_monotonic: float | None = None
        self._sequence = initial_sequence

    def process(
        self,
        analysis: BehaviorAnalysis,
        *,
        at: datetime,
        monotonic_now: float,
    ) -> EngineSnapshot:
        at = self._utc(at)
        self._failure_started_monotonic = None
        quality = ObservationQuality(
            dog_visible=analysis.dog_visible,
            dogs_detected=analysis.dogs_detected,
            observation_quality=analysis.observation_quality,
            body_visibility=analysis.body_visibility,
            face_visibility=analysis.face_visibility,
        )
        self._last_quality = quality

        issue = self._quality_issue(analysis)
        if issue is not None:
            status, reason = issue
            return self._quality_snapshot(
                status=status,
                reason=reason,
                quality=quality,
                at=at,
                monotonic_now=monotonic_now,
            )

        self._issue_key = None
        self._issue_started_monotonic = None
        self.status = MonitoringStatus.ANALYZING
        self._last_activity = analysis.activity
        self._last_signals = tuple(analysis.signals)

        raw_scores = analysis.state_scores.as_dict()
        if not self.smoothed_scores:
            self.smoothed_scores = dict(raw_scores)
        else:
            self.smoothed_scores = {
                label: self.alpha * raw_scores[label]
                + (1.0 - self.alpha) * self.smoothed_scores.get(label, 0.0)
                for label in StateLabel
            }

        ranked = sorted(self.smoothed_scores.items(), key=lambda item: item[1], reverse=True)
        candidate, candidate_score = ranked[0]
        runner_up_score = ranked[1][1]
        forced_indeterminate = self._requires_indeterminate(analysis, raw_scores)
        if forced_indeterminate:
            candidate = StateLabel.INDETERMINATE
            candidate_score = max(
                self.smoothed_scores[StateLabel.INDETERMINATE],
                analysis.state.confidence
                if analysis.state.label is StateLabel.INDETERMINATE
                else 0.0,
            )

        qualifies = forced_indeterminate or (
            candidate_score + 1e-12 >= self.transition_threshold
            and candidate_score - runner_up_score + 1e-12 >= self.transition_margin
        )
        if qualifies:
            if candidate is self.pending_candidate:
                self.pending_count += 1
            else:
                self.pending_candidate = candidate
                self.pending_count = 1
        else:
            self.pending_candidate = None
            self.pending_count = 0

        changed = False
        previous = self.stable_label
        reason = TransitionReason.UNCHANGED
        closed_event: StateEvent | None = None
        if (
            qualifies
            and self.pending_count >= self.min_candidate_results
            and candidate is not self.stable_label
        ):
            closed_event = self._close_event(at)
            self.stable_label = candidate
            self.stable_started_at = at
            self.current_event = StateEvent(state=candidate, started_at=at)
            self.current_event.observe(candidate_score)
            changed = True
            reason = (
                TransitionReason.INITIAL if previous is None else TransitionReason.STATE_CHANGED
            )
            self.pending_candidate = None
            self.pending_count = 0
        elif self.stable_label is None:
            reason = TransitionReason.CANDIDATE_PENDING
        elif self.current_event is not None:
            stable_confidence = self.smoothed_scores.get(self.stable_label, 0.0)
            self.current_event.observe(stable_confidence)

        stable_state = self._stable_state(at)
        alert = self._maybe_alert(at, stable_state)
        return self._snapshot(
            activity=analysis.activity,
            state=stable_state,
            signals=tuple(analysis.signals),
            quality=quality,
            transition=StateTransition(changed=changed, previous_state=previous, reason=reason),
            closed_event=closed_event,
            alert=alert,
        )

    def record_inference_failure(
        self,
        *,
        at: datetime,
        monotonic_now: float,
        attempt_started_monotonic: float | None = None,
    ) -> EngineSnapshot:
        at = self._utc(at)
        if self._failure_started_monotonic is None:
            self._failure_started_monotonic = (
                attempt_started_monotonic
                if attempt_started_monotonic is not None
                else monotonic_now
            )
        elapsed = monotonic_now - self._failure_started_monotonic
        if elapsed + 1e-12 < self.degradation_seconds:
            return self._snapshot(
                activity=self._last_activity,
                state=self._stable_state(at),
                signals=self._last_signals,
                quality=self._last_quality,
                transition=StateTransition(
                    changed=False,
                    previous_state=self.stable_label,
                    reason=TransitionReason.UNCHANGED,
                ),
            )

        previous = self.stable_label
        closed = self._close_event(at)
        self._reset_behavior()
        self.status = MonitoringStatus.SERVICE_DEGRADED
        return self._snapshot(
            activity=None,
            state=None,
            signals=(),
            quality=None,
            transition=StateTransition(
                changed=closed is not None,
                previous_state=previous,
                reason=TransitionReason.SERVICE_DEGRADED,
            ),
            closed_event=closed,
        )

    def camera_offline(self, *, at: datetime) -> EngineSnapshot:
        return self._terminal_status(
            MonitoringStatus.CAMERA_OFFLINE,
            TransitionReason.CAMERA_OFFLINE,
            self._utc(at),
            close_event=True,
        )

    def stream_unstable(self, *, at: datetime) -> EngineSnapshot:
        return self._terminal_status(
            MonitoringStatus.STREAM_UNSTABLE,
            TransitionReason.STREAM_UNSTABLE,
            self._utc(at),
            close_event=False,
        )

    def stop(self, *, at: datetime) -> EngineSnapshot:
        # There is no separate "stopped" monitoring status in v1; starting means inactive.
        return self._terminal_status(
            MonitoringStatus.STARTING,
            TransitionReason.STOPPED,
            self._utc(at),
            close_event=True,
        )

    def _quality_issue(
        self, analysis: BehaviorAnalysis
    ) -> tuple[MonitoringStatus, TransitionReason] | None:
        if not analysis.dog_visible or analysis.dogs_detected == 0:
            return MonitoringStatus.DOG_NOT_VISIBLE, TransitionReason.DOG_NOT_VISIBLE
        if analysis.dogs_detected > 1:
            return (
                MonitoringStatus.MULTIPLE_DOGS_DETECTED,
                TransitionReason.MULTIPLE_DOGS_DETECTED,
            )
        if (
            analysis.observation_quality < self.min_observation_quality
            or analysis.body_visibility < self.min_body_visibility
        ):
            return (
                MonitoringStatus.INSUFFICIENT_VISIBILITY,
                TransitionReason.INSUFFICIENT_VISIBILITY,
            )
        return None

    def _quality_snapshot(
        self,
        *,
        status: MonitoringStatus,
        reason: TransitionReason,
        quality: ObservationQuality,
        at: datetime,
        monotonic_now: float,
    ) -> EngineSnapshot:
        issue_key = status.value
        if self._issue_key != issue_key:
            self._issue_key = issue_key
            self._issue_started_monotonic = monotonic_now
        previous = self.stable_label
        closed: StateEvent | None = None
        if (
            self._issue_started_monotonic is not None
            and monotonic_now - self._issue_started_monotonic > self.absence_close_seconds
        ):
            closed = self._close_event(at)
            self._reset_behavior()
        self.status = status
        return self._snapshot(
            activity=None,
            state=None,
            signals=(),
            quality=quality,
            transition=StateTransition(
                changed=closed is not None,
                previous_state=previous,
                reason=reason,
            ),
            closed_event=closed,
        )

    def _requires_indeterminate(
        self,
        analysis: BehaviorAnalysis,
        scores: dict[StateLabel, float],
    ) -> bool:
        highest = max(scores, key=scores.get)  # type: ignore[arg-type]
        max_score = scores[highest]
        contradictory = highest is not analysis.state.label or any(
            "contradict" in limitation.casefold() for limitation in analysis.limitations
        )
        return (
            analysis.state.label is StateLabel.INDETERMINATE
            or max_score < self.indeterminate_max_confidence
            or contradictory
        )

    def _terminal_status(
        self,
        status: MonitoringStatus,
        reason: TransitionReason,
        at: datetime,
        *,
        close_event: bool,
    ) -> EngineSnapshot:
        previous = self.stable_label
        closed = self._close_event(at) if close_event else None
        if close_event:
            self._reset_behavior()
        self.status = status
        return self._snapshot(
            activity=None,
            state=None,
            signals=(),
            quality=None,
            transition=StateTransition(
                changed=closed is not None,
                previous_state=previous,
                reason=reason,
            ),
            closed_event=closed,
        )

    def _stable_state(self, at: datetime) -> StableState | None:
        if self.stable_label is None or self.stable_started_at is None:
            return None
        return StableState(
            label=self.stable_label,
            confidence=max(0.0, min(1.0, self.smoothed_scores.get(self.stable_label, 0.0))),
            duration_seconds=max(0, int((at - self.stable_started_at).total_seconds())),
            started_at=self.stable_started_at,
        )

    def _maybe_alert(self, at: datetime, state: StableState | None) -> bool:
        event = self.current_event
        if (
            state is None
            or event is None
            or event.alert_emitted
            or state.label is not StateLabel.STRESS_SIGNALS
            or state.confidence + 1e-12 < self.stress_alert_confidence
            or (at - event.started_at).total_seconds() + 1e-12 < self.stress_alert_seconds
        ):
            return False
        event.alert_emitted = True
        return True

    def _close_event(self, at: datetime) -> StateEvent | None:
        event = self.current_event
        if event is not None:
            event.close(at)
        self.current_event = None
        return event

    def _reset_behavior(self) -> None:
        self.smoothed_scores = {}
        self.stable_label = None
        self.stable_started_at = None
        self.pending_candidate = None
        self.pending_count = 0
        self.current_event = None
        self._last_activity = None
        self._last_signals = ()

    def _snapshot(
        self,
        *,
        activity,
        state,
        signals,
        quality,
        transition,
        closed_event=None,
        alert=False,
    ) -> EngineSnapshot:
        self._sequence += 1
        return EngineSnapshot(
            monitoring_status=self.status,
            transition_seq=self._sequence,
            activity=activity,
            state=state,
            signals=signals,
            quality=quality,
            transition=transition,
            closed_event=closed_event,
            alert=alert,
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("engine timestamps must include a timezone")
        return value.astimezone(UTC)
