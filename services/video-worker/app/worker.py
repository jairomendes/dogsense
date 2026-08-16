from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from app.api import AnalysisPublisher, PublisherError
from app.capture import AnalysisWindow, FrameRingBuffer, OpenCVFrameSource
from app.config import WorkerSettings
from app.contracts import (
    AnalysisMetadata,
    MonitoringStatus,
    WorkerIngest,
)
from app.inference import BehaviorAnalyzer, InferenceError, InferenceResult
from app.sampling import LatestOnlyQueue, TemporalSampler
from app.state_engine import EngineSnapshot, TemporalStateEngine

logger = logging.getLogger(__name__)


class FrameSource(Protocol):
    def frames(self) -> Any: ...


@dataclass(slots=True)
class WorkerRuntime:
    running: bool = False
    ready: bool = False
    status: MonitoringStatus = MonitoringStatus.STARTING
    last_frame_at: datetime | None = None
    frames_received: int = 0
    frames_dropped: int = 0
    windows_scheduled: int = 0
    windows_insufficient: int = 0
    windows_dropped: int = 0
    queue_depth: int = 0
    inference_requests: int = 0
    inference_errors: int = 0
    invalidated_results: int = 0
    publications: int = 0
    publication_errors: int = 0
    last_published_transition_seq: int | None = None
    latest_scheduled_sequence: int = 0
    discard_through_sequence: int = -1
    inference_in_flight: bool = False


class VideoAnalysisWorker:
    def __init__(
        self,
        *,
        settings: WorkerSettings,
        analyzer: BehaviorAnalyzer,
        publisher: AnalysisPublisher,
        source_factory: Callable[[], FrameSource] | None = None,
        engine: TemporalStateEngine | None = None,
        runtime: WorkerRuntime | None = None,
    ) -> None:
        self.settings = settings
        self.analyzer = analyzer
        self.publisher = publisher
        self.source_factory = source_factory or self._default_source
        self.engine = engine or TemporalStateEngine(
            alpha=0.35,
            degradation_seconds=settings.degradation_seconds,
            # A wall-clock base preserves ordering when the stateless worker
            # restarts while the API retains the previous session sequence.
            # Epoch milliseconds remain below JavaScript's safe-integer limit.
            initial_sequence=time.time_ns() // 1_000_000,
        )
        self.runtime = runtime or WorkerRuntime()
        self.buffer = FrameRingBuffer(settings.buffer_capacity)
        self.sampler = TemporalSampler(
            window_seconds=settings.frame_window_seconds,
            sample_fps=settings.frame_sample_fps,
            min_frames=settings.min_frames,
            max_frames=settings.max_frames,
            duplicate_hamming_distance=settings.duplicate_hamming_distance,
        )
        self.windows: LatestOnlyQueue[AnalysisWindow] = LatestOnlyQueue()
        self._stop = asyncio.Event()
        self._publish_lock = asyncio.Lock()
        self._window_sequence = 0

    async def run(self) -> None:
        self.runtime.running = True
        tasks = [
            asyncio.create_task(self._capture_loop(), name="capture"),
            asyncio.create_task(self._schedule_loop(), name="scheduler"),
            asyncio.create_task(self._inference_loop(), name="inference"),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            self._stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            snapshot = self.engine.stop(at=datetime.now(UTC))
            await self._publish_snapshot(
                snapshot,
                analysis_id=uuid4(),
                captured_at=datetime.now(UTC),
            )
            self.runtime.running = False
            self.runtime.ready = False

    def request_stop(self) -> None:
        self._stop.set()

    async def process_window(self, window: AnalysisWindow) -> WorkerIngest | None:
        """Analyze one window; exposed for deterministic component testing."""

        loop = asyncio.get_running_loop()
        started = loop.time()
        self.runtime.inference_in_flight = True
        self.runtime.inference_requests += 1
        try:
            result = await self.analyzer.analyze(window)
            if (
                window.sequence <= self.runtime.discard_through_sequence
                or window.sequence < self.runtime.latest_scheduled_sequence
            ):
                self.runtime.invalidated_results += 1
                return None
            snapshot = self.engine.process(
                result.analysis,
                at=window.captured_at,
                monotonic_now=window.frames[-1].monotonic_ts,
            )
            # The internal API persists consolidated states only. Candidate-pending
            # results remain worker-local instead of being mislabeled as analyzing
            # with a fabricated or null behavioral state.
            if snapshot.monitoring_status is MonitoringStatus.ANALYZING and snapshot.state is None:
                return None
            payload = self._build_payload(
                snapshot,
                analysis_id=window.analysis_id,
                captured_at=window.captured_at,
                result=result,
            )
            await self._publish(payload)
            return payload
        except asyncio.CancelledError:
            raise
        except InferenceError:
            self.runtime.inference_errors += 1
            now = datetime.now(UTC)
            snapshot = self.engine.record_inference_failure(
                at=now,
                monotonic_now=loop.time(),
                attempt_started_monotonic=started,
            )
            if snapshot.monitoring_status is MonitoringStatus.SERVICE_DEGRADED:
                payload = self._build_payload(
                    snapshot,
                    analysis_id=window.analysis_id,
                    captured_at=now,
                    result=None,
                )
                await self._publish(payload)
                return payload
            return None
        finally:
            self.runtime.inference_in_flight = False

    async def _capture_loop(self) -> None:
        backoff = 1.0
        repeated_since: float | None = None
        last_frame = None
        unstable_published = False
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            try:
                source = self.source_factory()
                async for frame in source.frames():
                    if self._stop.is_set():
                        return
                    self.runtime.frames_received += 1
                    self.runtime.last_frame_at = frame.captured_at
                    exact_repeat = (
                        last_frame is not None
                        and frame.fingerprint == last_frame.fingerprint
                        and frame.jpeg == last_frame.jpeg
                    )
                    if exact_repeat:
                        repeated_since = repeated_since or loop.time()
                        if (
                            not unstable_published
                            and loop.time() - repeated_since >= self.settings.stream_freeze_seconds
                        ):
                            self._invalidate_pending_windows()
                            snapshot = self.engine.stream_unstable(at=frame.captured_at)
                            await self._publish_snapshot(
                                snapshot,
                                analysis_id=uuid4(),
                                captured_at=frame.captured_at,
                            )
                            unstable_published = True
                            self.runtime.ready = False
                    else:
                        repeated_since = None
                        unstable_published = False
                        self.runtime.ready = True
                    last_frame = frame
                    if self.buffer.append(frame):
                        if not unstable_published:
                            self.runtime.status = MonitoringStatus.ANALYZING
                    else:
                        self.runtime.frames_dropped += 1
                    backoff = 1.0
                raise RuntimeError("configured stream ended")
            except asyncio.CancelledError:
                raise
            except Exception:
                self.runtime.ready = False
                self._invalidate_pending_windows()
                last_frame = None
                repeated_since = None
                unstable_published = False
                snapshot = self.engine.camera_offline(at=datetime.now(UTC))
                await self._publish_snapshot(
                    snapshot,
                    analysis_id=uuid4(),
                    captured_at=datetime.now(UTC),
                )
                logger.warning(
                    "camera unavailable",
                    extra={"event": "camera_offline", "retry_seconds": backoff},
                )
                await self._wait_or_stop(backoff)
                backoff = min(30.0, backoff * 2.0)

    async def _schedule_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            await self._wait_or_stop(self.settings.analysis_interval_seconds)
            if self._stop.is_set():
                return
            latest = self.buffer.latest
            if (
                latest is not None
                and loop.time() - latest.monotonic_ts >= self.settings.stream_freeze_seconds
            ):
                self._invalidate_pending_windows()
                self.runtime.ready = False
                snapshot = self.engine.stream_unstable(at=datetime.now(UTC))
                await self._publish_snapshot(
                    snapshot,
                    analysis_id=uuid4(),
                    captured_at=datetime.now(UTC),
                )
                continue
            selected = self.sampler.select(
                self.buffer.recent(
                    self.settings.frame_window_seconds,
                    now=loop.time(),
                )
            )
            if not selected:
                self.runtime.windows_insufficient += 1
                continue
            self._window_sequence += 1
            window = AnalysisWindow(
                sequence=self._window_sequence,
                analysis_id=uuid4(),
                session_id=self.settings.session_id,
                camera_id=self.settings.camera_id,
                frames=tuple(selected),
                captured_at=selected[-1].captured_at,
            )
            self.runtime.windows_scheduled += 1
            self.runtime.latest_scheduled_sequence = window.sequence
            before = self.windows.dropped
            await self.windows.put_latest(window)
            self.runtime.windows_dropped += self.windows.dropped - before
            self.runtime.queue_depth = self.windows.qsize

    async def _inference_loop(self) -> None:
        while not self._stop.is_set():
            window = await self.windows.get()
            self.runtime.queue_depth = self.windows.qsize
            try:
                await self.process_window(window)
            except PublisherError:
                self.runtime.publication_errors += 1
                logger.error("publication unavailable", extra={"event": "analysis_publish_failed"})
            finally:
                self.windows.task_done()

    def _invalidate_pending_windows(self) -> None:
        self.buffer.clear()
        self.windows.clear()
        self.runtime.queue_depth = self.windows.qsize
        self.runtime.discard_through_sequence = self.runtime.latest_scheduled_sequence

    async def _publish_snapshot(
        self,
        snapshot: EngineSnapshot,
        *,
        analysis_id,
        captured_at: datetime,
    ) -> None:
        payload = self._build_payload(
            snapshot,
            analysis_id=analysis_id,
            captured_at=captured_at,
            result=None,
        )
        try:
            await self._publish(payload)
        except PublisherError:
            self.runtime.publication_errors += 1
            logger.error("status publication unavailable", extra={"event": "status_publish_failed"})

    async def _publish(self, payload: WorkerIngest) -> None:
        async with self._publish_lock:
            await self.publisher.publish(payload)
            self.runtime.publications += 1
            self.runtime.last_published_transition_seq = payload.transition_seq
            self.runtime.status = payload.monitoring_status

    def _build_payload(
        self,
        snapshot: EngineSnapshot,
        *,
        analysis_id,
        captured_at: datetime,
        result: InferenceResult | None,
    ) -> WorkerIngest:
        metadata = (
            AnalysisMetadata(
                model=result.model,
                latency_ms=result.latency_ms,
                prompt_version=result.prompt_version,
                schema_version=result.schema_version,
            )
            if result is not None
            else None
        )
        return WorkerIngest(
            analysis_id=analysis_id,
            session_id=self.settings.session_id,
            camera_id=self.settings.camera_id,
            captured_at=captured_at,
            transition_seq=snapshot.transition_seq,
            monitoring_status=snapshot.monitoring_status,
            activity=snapshot.activity,
            state=snapshot.state,
            signals=list(snapshot.signals),
            quality=snapshot.quality,
            analysis=metadata,
            transition=snapshot.transition,
        )

    async def _wait_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    def _default_source(self) -> OpenCVFrameSource:
        return OpenCVFrameSource(
            self.settings.stream_url,
            width=self.settings.image_width,
            height=self.settings.image_height,
            jpeg_quality=self.settings.jpeg_quality,
        )
