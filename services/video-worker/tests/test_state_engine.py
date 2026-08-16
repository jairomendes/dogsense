from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import load_analysis

from app.contracts import BehaviorAnalysis, MonitoringStatus, StateLabel
from app.state_engine import TemporalStateEngine

BASE = datetime(2026, 8, 15, 15, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


def with_scores(
    analysis: BehaviorAnalysis,
    *,
    label: str,
    top: float,
    runner_label: str = "alert",
    runner: float = 0.1,
) -> BehaviorAnalysis:
    raw = analysis.model_dump(mode="json")
    raw["state"] = {"label": label, "confidence": top}
    raw["state_scores"] = {key: 0.01 for key in raw["state_scores"]}
    raw["state_scores"][label] = top
    if runner_label != label:
        raw["state_scores"][runner_label] = runner
    return BehaviorAnalysis.model_validate(raw)


def consolidate(
    engine: TemporalStateEngine,
    analysis: BehaviorAnalysis,
    *,
    start: float = 0,
):
    first = engine.process(analysis, at=at(start), monotonic_now=start)
    second = engine.process(analysis, at=at(start + 1), monotonic_now=start + 1)
    return first, second


def test_initial_state_requires_two_consecutive_results() -> None:
    engine = TemporalStateEngine()
    first, second = consolidate(engine, load_analysis("relaxed.json"))
    assert first.state is None
    assert first.transition.reason.value == "candidate_pending"
    assert second.state is not None
    assert second.state.label is StateLabel.RELAXED
    assert second.transition.changed
    assert second.transition.reason.value == "initial"


def test_initial_sequence_preserves_order_across_worker_restart() -> None:
    previous_process_sequence = 1_700_000_000_123
    engine = TemporalStateEngine(initial_sequence=previous_process_sequence)
    first, second = consolidate(engine, load_analysis("relaxed.json"))
    assert first.transition_seq == previous_process_sequence + 1
    assert second.transition_seq == previous_process_sequence + 2


@pytest.mark.parametrize(
    ("top", "runner", "expected"),
    [
        (0.6499, 0.50, False),
        (0.65, 0.55, True),
        (0.70, 0.6001, False),
        (0.70, 0.60, True),
    ],
)
def test_transition_threshold_and_margin_edges(top: float, runner: float, expected: bool) -> None:
    engine = TemporalStateEngine(alpha=0.35)
    analysis = with_scores(
        load_analysis("relaxed.json"),
        label="relaxed",
        top=top,
        runner=runner,
    )
    _, second = consolidate(engine, analysis)
    assert (second.state is not None) is expected


def test_ewma_formula_and_isolated_divergence_do_not_flip() -> None:
    engine = TemporalStateEngine(alpha=0.35)
    relaxed = load_analysis("relaxed.json")
    _, stable = consolidate(engine, relaxed)
    before_relaxed = engine.smoothed_scores[StateLabel.RELAXED]
    alert = load_analysis("alert.json")
    divergent = engine.process(alert, at=at(2), monotonic_now=2)
    expected = 0.35 * alert.state_scores.relaxed + 0.65 * before_relaxed
    assert engine.smoothed_scores[StateLabel.RELAXED] == pytest.approx(expected)
    assert divergent.state is not None
    assert divergent.state.label is StateLabel.RELAXED
    assert not divergent.transition.changed


def test_persistent_candidate_eventually_transitions_and_closes_event() -> None:
    engine = TemporalStateEngine()
    consolidate(engine, load_analysis("relaxed.json"))
    alert = load_analysis("alert.json")
    snapshots = [engine.process(alert, at=at(i), monotonic_now=i) for i in range(2, 9)]
    changed = [snapshot for snapshot in snapshots if snapshot.transition.changed]
    assert len(changed) == 1
    transition = changed[0]
    assert transition.state is not None and transition.state.label is StateLabel.ALERT
    assert transition.closed_event is not None
    assert transition.closed_event.state is StateLabel.RELAXED


def test_no_dog_hides_state_immediately_and_closes_event_after_ten_seconds() -> None:
    engine = TemporalStateEngine()
    consolidate(engine, load_analysis("relaxed.json"))
    absent = load_analysis("dog-not-visible.json")
    initial = engine.process(absent, at=at(2), monotonic_now=2)
    boundary = engine.process(absent, at=at(12), monotonic_now=12)
    after = engine.process(absent, at=at(12.001), monotonic_now=12.001)
    assert initial.monitoring_status is MonitoringStatus.DOG_NOT_VISIBLE
    assert initial.state is None and boundary.closed_event is None
    assert after.closed_event is not None
    assert after.transition.changed
    assert engine.stable_label is None


def test_multiple_dogs_and_low_visibility_are_technical_statuses() -> None:
    engine = TemporalStateEngine()
    multiple = engine.process(load_analysis("multiple-dogs.json"), at=at(0), monotonic_now=0)
    low = engine.process(load_analysis("insufficient-visibility.json"), at=at(1), monotonic_now=1)
    assert multiple.monitoring_status is MonitoringStatus.MULTIPLE_DOGS_DETECTED
    assert low.monitoring_status is MonitoringStatus.INSUFFICIENT_VISIBILITY
    assert multiple.state is None and low.state is None


def test_repeated_inference_failure_holds_then_degrades() -> None:
    engine = TemporalStateEngine(degradation_seconds=10)
    consolidate(engine, load_analysis("relaxed.json"))
    held = engine.record_inference_failure(at=at(9), monotonic_now=9, attempt_started_monotonic=0)
    degraded = engine.record_inference_failure(at=at(10), monotonic_now=10)
    assert held.monitoring_status is MonitoringStatus.ANALYZING
    assert held.state is not None and held.state.label is StateLabel.RELAXED
    assert degraded.monitoring_status is MonitoringStatus.SERVICE_DEGRADED
    assert degraded.state is None
    assert degraded.closed_event is not None


def test_camera_offline_closes_immediately_and_stream_unstable_does_not() -> None:
    engine = TemporalStateEngine()
    consolidate(engine, load_analysis("relaxed.json"))
    unstable = engine.stream_unstable(at=at(2))
    assert unstable.monitoring_status is MonitoringStatus.STREAM_UNSTABLE
    assert unstable.closed_event is None
    offline = engine.camera_offline(at=at(3))
    assert offline.monitoring_status is MonitoringStatus.CAMERA_OFFLINE
    assert offline.closed_event is not None
    assert engine.stable_label is None


def test_stress_alert_emits_once_per_event_after_duration() -> None:
    engine = TemporalStateEngine()
    stress = load_analysis("stress-signals.json")
    consolidate(engine, stress)
    alert = engine.process(stress, at=at(11), monotonic_now=11)
    repeat = engine.process(stress, at=at(12), monotonic_now=12)
    assert alert.alert
    assert not repeat.alert


def test_low_maximum_confidence_becomes_indeterminate_after_persistence() -> None:
    engine = TemporalStateEngine()
    uncertain = with_scores(
        load_analysis("relaxed.json"),
        label="relaxed",
        top=0.54,
        runner_label="alert",
        runner=0.2,
    )
    _, second = consolidate(engine, uncertain)
    assert second.state is not None
    assert second.state.label is StateLabel.INDETERMINATE
