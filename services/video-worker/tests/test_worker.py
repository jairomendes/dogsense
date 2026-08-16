from __future__ import annotations

import asyncio
import json
from pathlib import Path

import jsonschema
import pytest
from conftest import load_analysis

from app.config import WorkerSettings
from app.contracts import MonitoringStatus, StateLabel
from app.inference import InferenceError, InferenceResult
from app.state_engine import TemporalStateEngine
from app.worker import VideoAnalysisWorker, WorkerRuntime

ROOT = Path(__file__).resolve().parents[3]


class RecordingPublisher:
    def __init__(self) -> None:
        self.messages = []

    async def publish(self, payload) -> None:
        self.messages.append(payload)


class FixedAnalyzer:
    def __init__(self, analysis) -> None:
        self.analysis = analysis

    async def analyze(self, window):
        return InferenceResult(
            analysis=self.analysis,
            model="fake-test",
            latency_ms=12,
        )


class BlockingAnalyzer(FixedAnalyzer):
    def __init__(self, analysis) -> None:
        super().__init__(analysis)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def analyze(self, window):
        self.started.set()
        await self.release.wait()
        return await super().analyze(window)


class FailingAnalyzer:
    async def analyze(self, window):
        await asyncio.sleep(0.005)
        raise InferenceError("fixture unavailable")


def make_worker(analyzer, publisher=None, *, engine=None):
    runtime = WorkerRuntime()
    return VideoAnalysisWorker(
        settings=WorkerSettings(),
        analyzer=analyzer,
        publisher=publisher or RecordingPublisher(),
        runtime=runtime,
        engine=engine,
    )


@pytest.mark.asyncio
async def test_valid_windows_publish_stable_contract_with_versions(window_factory) -> None:
    publisher = RecordingPublisher()
    worker = make_worker(FixedAnalyzer(load_analysis("relaxed.json")), publisher)
    first_window = window_factory(1)
    worker.runtime.latest_scheduled_sequence = 1
    first = await worker.process_window(first_window)
    second_window = window_factory(2)
    worker.runtime.latest_scheduled_sequence = 2
    second = await worker.process_window(second_window)
    assert first is None
    assert second is not None and second.state is not None
    assert second.state.label is StateLabel.RELAXED
    assert second.analysis is not None
    assert second.analysis.prompt_version == "behavior-observer-v1"
    assert second.analysis.schema_version == "behavior-analysis-v1"
    assert len(publisher.messages) == 1
    assert worker.runtime.inference_requests == 2
    assert worker.runtime.publications == 1
    assert worker.runtime.last_published_transition_seq == second.transition_seq
    assert second.transition_seq > 1_000_000_000_000

    schema = json.loads(
        (ROOT / "packages/contracts/json-schema/worker-ingest-v1.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(second.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_obsolete_in_flight_result_is_discarded(window_factory) -> None:
    publisher = RecordingPublisher()
    analyzer = BlockingAnalyzer(load_analysis("relaxed.json"))
    worker = make_worker(analyzer, publisher)
    worker.runtime.latest_scheduled_sequence = 1
    task = asyncio.create_task(worker.process_window(window_factory(1)))
    await analyzer.started.wait()
    worker.runtime.latest_scheduled_sequence = 2
    analyzer.release.set()
    assert await task is None
    assert worker.runtime.invalidated_results == 1
    assert publisher.messages == []
    assert worker.engine.stable_label is None


@pytest.mark.asyncio
async def test_invalidated_stream_window_cannot_mutate_state(window_factory) -> None:
    publisher = RecordingPublisher()
    worker = make_worker(FixedAnalyzer(load_analysis("relaxed.json")), publisher)
    worker.runtime.latest_scheduled_sequence = 5
    worker.runtime.discard_through_sequence = 5
    assert await worker.process_window(window_factory(5)) is None
    assert publisher.messages == []


@pytest.mark.asyncio
async def test_slow_failed_inference_publishes_service_degraded_without_fake_state(
    window_factory,
) -> None:
    publisher = RecordingPublisher()
    engine = TemporalStateEngine(degradation_seconds=0.001)
    worker = make_worker(FailingAnalyzer(), publisher, engine=engine)
    worker.runtime.latest_scheduled_sequence = 1
    result = await worker.process_window(window_factory(1))
    assert result is not None
    assert result.monitoring_status is MonitoringStatus.SERVICE_DEGRADED
    assert result.activity is None
    assert result.state is None
    assert result.analysis is None
    assert result.signals == []


@pytest.mark.asyncio
async def test_worker_never_runs_more_than_one_process_window_in_inference_loop(
    window_factory,
) -> None:
    # The component method can be called independently, but the production queue has one consumer.
    publisher = RecordingPublisher()
    worker = make_worker(FixedAnalyzer(load_analysis("relaxed.json")), publisher)
    await worker.windows.put_latest(window_factory(1))
    await worker.windows.put_latest(window_factory(2))
    assert worker.windows.qsize == 1
    assert worker.windows.dropped == 1
