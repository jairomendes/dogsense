from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.config import Settings
from app.models import (
    ActivityValue,
    AnalysisIngest,
    AnalysisMetadata,
    Camera,
    CurrentState,
    Dog,
    Feedback,
    FeedbackCreate,
    IngestResponse,
    MonitoringSession,
    MonitoringStatus,
    QualityValue,
    Receipt,
    SessionStatus,
    Signal,
    SpeechAsset,
    StateEvent,
    StateValue,
    utcnow,
)
from app.security import SecretBox, redact_rtsp_url


def _demo_id(kind: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"https://dogsense.local/demo/{kind}"))


DEMO_CAMERA_ID = "00000000-0000-0000-0000-000000000001"
DEMO_DOG_ID = "00000000-0000-0000-0000-000000000002"
DEMO_SESSION_ID = "00000000-0000-0000-0000-000000000003"
DEMO_EVENT_ID = "00000000-0000-0000-0000-000000000004"

ALIASES = {
    "demo-dog": DEMO_DOG_ID,
    "demo-camera": DEMO_CAMERA_ID,
    "demo-session": DEMO_SESSION_ID,
    "demo-event": DEMO_EVENT_ID,
}


class StoreNotFound(KeyError):
    pass


class IngestConflict(ValueError):
    pass


class MemoryStore:
    """Single-process transactional store used by demo and tests.

    Every multi-object mutation is protected by one asyncio lock. PostgresStore
    extends it with a durable JSONB snapshot, which keeps the exact semantics for
    the MVP's single API process while leaving repository selection configurable.
    """

    backend = "memory"

    def __init__(self, settings: Settings, secret_box: SecretBox):
        self.settings = settings
        self.secret_box = secret_box
        self._lock = asyncio.Lock()
        self.ready = False
        self.dogs: dict[str, Dog] = {}
        self.cameras: dict[str, Camera] = {}
        self.sessions: dict[str, MonitoringSession] = {}
        self.current_states: dict[str, CurrentState] = {}
        self.events: dict[str, StateEvent] = {}
        self.feedback: dict[str, Feedback] = {}
        self.speeches: dict[str, SpeechAsset] = {}
        self.receipts: dict[str, Receipt] = {}
        self.processed_analyses: dict[str, IngestResponse] = {}
        self.last_transition_seq: dict[str, int] = {}
        self.last_captured_at: dict[str, str] = {}

    async def initialize(self) -> None:
        self.ready = True

    async def close(self) -> None:
        self.ready = False

    def resolve_id(self, value: str) -> str:
        return ALIASES.get(value, value)

    async def _changed(self) -> None:
        return None

    async def seed_demo(self) -> None:
        async with self._lock:
            if DEMO_DOG_ID in self.dogs:
                return
            now = utcnow()
            dog = Dog(id=DEMO_DOG_ID, name="Luna", timezone="America/Sao_Paulo", created_at=now, updated_at=now)
            encrypted = self.secret_box.encrypt(
                {"rtsp_url": "rtsp://demo.invalid/dog-camera", "username": None, "password": None}
            )
            camera = Camera(
                id=DEMO_CAMERA_ID,
                dog_id=dog.id,
                name="Demo camera",
                active=True,
                source_type="rtsp",
                rtsp_url_redacted="rtsp://demo.invalid/***",
                has_credentials=False,
                encrypted_credentials=encrypted,
                status=MonitoringStatus.starting,
                created_at=now,
                updated_at=now,
            )
            session = MonitoringSession(
                id=DEMO_SESSION_ID,
                dog_id=dog.id,
                camera_id=camera.id,
                status=SessionStatus.active,
                started_at=now - timedelta(minutes=8),
            )
            event = StateEvent(
                id=DEMO_EVENT_ID,
                dog_id=dog.id,
                camera_id=camera.id,
                session_id=session.id,
                started_at=now - timedelta(minutes=7),
                ended_at=now - timedelta(minutes=4),
                activity="resting",
                state="relaxed",
                confidence_avg=0.87,
                confidence_max=0.92,
                observation_quality_avg=0.89,
                signals=[
                    Signal(name="low_motion", confidence=0.92),
                    Signal(name="loose_body_posture", confidence=0.84),
                ],
                prompt_version="behavior-observer-v1",
                model_name="dogsense-demo-fixture",
                source="demo",
                created_at=now - timedelta(minutes=7),
                sample_count=4,
            )
            current = CurrentState(
                dog_id=dog.id,
                camera_id=camera.id,
                session_id=session.id,
                monitoring_status=MonitoringStatus.analyzing,
                activity=ActivityValue(label="resting", confidence=0.91),
                state=StateValue(
                    label="relaxed",
                    confidence=0.86,
                    duration_seconds=240,
                    started_at=now - timedelta(minutes=4),
                ),
                signals=[
                    Signal(name="low_motion", confidence=0.92),
                    Signal(name="loose_body_posture", confidence=0.84),
                ],
                quality=QualityValue(
                    dog_visible=True,
                    dogs_detected=1,
                    observation_quality=0.89,
                    body_visibility=0.93,
                    face_visibility=0.38,
                ),
                analysis=AnalysisMetadata(
                    schema_version="behavior-analysis-v1",
                    prompt_version="behavior-observer-v1",
                    model="dogsense-demo-fixture",
                    latency_ms=120,
                ),
                sequence=0,
                captured_at=now,
                updated_at=now,
            )
            self.dogs[dog.id] = dog
            self.cameras[camera.id] = camera
            self.sessions[session.id] = session
            self.events[event.id] = event
            self.current_states[dog.id] = current
            self.last_transition_seq[session.id] = -1
            await self._changed()

    async def create_dog(self, name: str, timezone_name: str) -> Dog:
        async with self._lock:
            now = utcnow()
            dog = Dog(id=str(uuid4()), name=name, timezone=timezone_name, created_at=now, updated_at=now)
            self.dogs[dog.id] = dog
            await self._changed()
            return dog.model_copy(deep=True)

    async def get_dog(self, dog_id: str) -> Dog:
        resolved = self.resolve_id(dog_id)
        dog = self.dogs.get(resolved)
        if not dog:
            raise StoreNotFound("dog")
        return dog.model_copy(deep=True)

    async def update_dog(self, dog_id: str, changes: dict[str, Any]) -> Dog:
        async with self._lock:
            dog = self.dogs.get(self.resolve_id(dog_id))
            if not dog:
                raise StoreNotFound("dog")
            updated = dog.model_copy(update={**changes, "updated_at": utcnow()})
            self.dogs[updated.id] = updated
            await self._changed()
            return updated.model_copy(deep=True)

    async def create_camera(
        self,
        *,
        dog_id: str,
        name: str,
        rtsp_url: str,
        username: str | None,
        password: str | None,
        active: bool,
    ) -> Camera:
        dog_id = self.resolve_id(dog_id)
        async with self._lock:
            if dog_id not in self.dogs:
                raise StoreNotFound("dog")
            now = utcnow()
            if active:
                for existing_id, existing in tuple(self.cameras.items()):
                    if existing.active:
                        self.cameras[existing_id] = existing.model_copy(update={"active": False, "updated_at": now})
            camera = Camera(
                id=str(uuid4()),
                dog_id=dog_id,
                name=name,
                active=active,
                source_type="rtsps" if rtsp_url.lower().startswith("rtsps://") else "rtsp",
                rtsp_url_redacted=redact_rtsp_url(rtsp_url),
                has_credentials=bool(username or password or "@" in rtsp_url),
                encrypted_credentials=self.secret_box.encrypt(
                    {"rtsp_url": rtsp_url, "username": username, "password": password}
                ),
                status=MonitoringStatus.starting,
                created_at=now,
                updated_at=now,
            )
            self.cameras[camera.id] = camera
            await self._changed()
            return camera.model_copy(deep=True)

    async def get_camera(self, camera_id: str) -> Camera:
        camera = self.cameras.get(self.resolve_id(camera_id))
        if not camera:
            raise StoreNotFound("camera")
        return camera.model_copy(deep=True)

    async def update_camera(self, camera_id: str, changes: dict[str, Any]) -> Camera:
        async with self._lock:
            resolved = self.resolve_id(camera_id)
            camera = self.cameras.get(resolved)
            if not camera:
                raise StoreNotFound("camera")
            now = utcnow()
            update: dict[str, Any] = {"updated_at": now}
            if changes.get("name") is not None:
                update["name"] = changes["name"]
            if changes.get("active") is not None:
                update["active"] = changes["active"]
                if changes["active"]:
                    for existing_id, existing in tuple(self.cameras.items()):
                        if existing_id != resolved and existing.active:
                            self.cameras[existing_id] = existing.model_copy(update={"active": False, "updated_at": now})
            secrets = self.secret_box.decrypt(camera.encrypted_credentials)
            changed_secret = False
            for key in ("rtsp_url", "username", "password"):
                if changes.get(key) is not None:
                    secrets[key] = changes[key]
                    changed_secret = True
            if changed_secret:
                update.update(
                    encrypted_credentials=self.secret_box.encrypt(secrets),
                    rtsp_url_redacted=redact_rtsp_url(secrets["rtsp_url"]),
                    source_type="rtsps" if secrets["rtsp_url"].lower().startswith("rtsps://") else "rtsp",
                    has_credentials=bool(
                        secrets.get("username") or secrets.get("password") or "@" in secrets["rtsp_url"]
                    ),
                )
            camera = camera.model_copy(update=update)
            self.cameras[resolved] = camera
            await self._changed()
            return camera.model_copy(deep=True)

    async def set_camera_check(self, camera_id: str, success: bool) -> Camera:
        async with self._lock:
            resolved = self.resolve_id(camera_id)
            camera = self.cameras.get(resolved)
            if not camera:
                raise StoreNotFound("camera")
            now = utcnow()
            camera = camera.model_copy(
                update={
                    "status": MonitoringStatus.analyzing if success else MonitoringStatus.camera_offline,
                    "last_checked_at": now,
                    "updated_at": now,
                }
            )
            self.cameras[resolved] = camera
            await self._changed()
            return camera.model_copy(deep=True)

    async def start_session(self, dog_id: str, camera_id: str) -> MonitoringSession:
        dog_id, camera_id = self.resolve_id(dog_id), self.resolve_id(camera_id)
        async with self._lock:
            if dog_id not in self.dogs:
                raise StoreNotFound("dog")
            camera = self.cameras.get(camera_id)
            if not camera or camera.dog_id != dog_id:
                raise StoreNotFound("camera")
            for session in self.sessions.values():
                if session.dog_id == dog_id and session.status == SessionStatus.active:
                    return session.model_copy(deep=True)
            session = MonitoringSession(
                id=str(uuid4()), dog_id=dog_id, camera_id=camera_id, status=SessionStatus.active, started_at=utcnow()
            )
            self.sessions[session.id] = session
            self.last_transition_seq[session.id] = -1
            now = utcnow()
            self.current_states[dog_id] = CurrentState(
                dog_id=dog_id,
                camera_id=camera_id,
                session_id=session.id,
                monitoring_status=MonitoringStatus.starting,
                activity=None,
                state=None,
                signals=[],
                quality=None,
                analysis=None,
                sequence=0,
                captured_at=now,
                updated_at=now,
            )
            await self._changed()
            return session.model_copy(deep=True)

    async def get_session(self, session_id: str) -> MonitoringSession:
        session = self.sessions.get(self.resolve_id(session_id))
        if not session:
            raise StoreNotFound("session")
        return session.model_copy(deep=True)

    async def stop_session(self, session_id: str) -> MonitoringSession:
        async with self._lock:
            resolved = self.resolve_id(session_id)
            session = self.sessions.get(resolved)
            if not session:
                raise StoreNotFound("session")
            if session.status == SessionStatus.stopped:
                return session.model_copy(deep=True)
            now = utcnow()
            session = session.model_copy(update={"status": SessionStatus.stopped, "stopped_at": now})
            self.sessions[resolved] = session
            self._close_open_event(session.dog_id, now)
            current = self.current_states.get(session.dog_id)
            if current:
                self.current_states[session.dog_id] = current.model_copy(
                    update={
                        "monitoring_status": MonitoringStatus.starting,
                        "activity": None,
                        "state": None,
                        "signals": [],
                        "quality": None,
                        "analysis": None,
                        "updated_at": now,
                    }
                )
            await self._changed()
            return session.model_copy(deep=True)

    async def get_current_state(self, dog_id: str) -> CurrentState:
        current = self.current_states.get(self.resolve_id(dog_id))
        if not current:
            raise StoreNotFound("current state")
        return current.model_copy(deep=True)

    async def list_events(
        self,
        dog_id: str,
        *,
        limit: int = 50,
        state: str | None = None,
        before: Any | None = None,
    ) -> list[StateEvent]:
        resolved = self.resolve_id(dog_id)
        if resolved not in self.dogs:
            raise StoreNotFound("dog")
        events = [event for event in self.events.values() if event.dog_id == resolved]
        if state:
            events = [event for event in events if event.state == state]
        if before:
            events = [event for event in events if event.started_at < before]
        ordered = sorted(events, key=lambda item: item.started_at, reverse=True)[:limit]
        return [event.model_copy(deep=True) for event in ordered]

    async def get_event(self, event_id: str) -> StateEvent:
        event = self.events.get(self.resolve_id(event_id))
        if not event:
            raise StoreNotFound("event")
        return event.model_copy(deep=True)

    def _open_event(self, current: CurrentState, event_id: str | None = None) -> StateEvent | None:
        if not current.state or not current.activity or not current.quality or not current.analysis:
            return None
        started_at = current.state.started_at or current.captured_at - timedelta(seconds=current.state.duration_seconds)
        event = StateEvent(
            id=event_id or str(uuid4()),
            dog_id=current.dog_id,
            camera_id=current.camera_id,
            session_id=current.session_id,
            started_at=started_at,
            ended_at=None,
            activity=current.activity.label,
            state=current.state.label,
            confidence_avg=current.state.confidence,
            confidence_max=current.state.confidence,
            observation_quality_avg=current.quality.observation_quality,
            signals=current.signals,
            prompt_version=current.analysis.prompt_version,
            model_name=current.analysis.model,
            created_at=utcnow(),
        )
        self.events[event.id] = event
        return event

    def _find_open_event(self, dog_id: str) -> StateEvent | None:
        candidates = [event for event in self.events.values() if event.dog_id == dog_id and event.ended_at is None]
        return max(candidates, key=lambda event: event.started_at) if candidates else None

    def _close_open_event(self, dog_id: str, ended_at: Any) -> StateEvent | None:
        event = self._find_open_event(dog_id)
        if not event:
            return None
        safe_end = max(ended_at, event.started_at)
        event = event.model_copy(update={"ended_at": safe_end})
        self.events[event.id] = event
        return event

    def _update_open_event(self, event: StateEvent, current: CurrentState) -> StateEvent:
        count = event.sample_count + 1
        confidence = current.state.confidence if current.state else event.confidence_avg
        quality = current.quality.observation_quality if current.quality else event.observation_quality_avg
        updated = event.model_copy(
            update={
                "confidence_avg": ((event.confidence_avg * event.sample_count) + confidence) / count,
                "confidence_max": max(event.confidence_max, confidence),
                "observation_quality_avg": ((event.observation_quality_avg * event.sample_count) + quality) / count,
                "signals": current.signals or event.signals,
                "sample_count": count,
            }
        )
        self.events[event.id] = updated
        return updated

    async def ingest(self, payload: AnalysisIngest) -> IngestResponse:
        analysis_id = str(payload.analysis_id)
        session_id = str(payload.session_id)
        camera_id = str(payload.camera_id)
        async with self._lock:
            duplicate = self.processed_analyses.get(analysis_id)
            if duplicate:
                return duplicate.model_copy(update={"accepted": False, "duplicate": True}, deep=True)
            session = self.sessions.get(session_id)
            if not session:
                raise StoreNotFound("session")
            if session.status != SessionStatus.active:
                raise IngestConflict("monitoring session is stopped")
            if session.camera_id != camera_id:
                raise IngestConflict("camera_id does not belong to monitoring session")
            previous_seq = self.last_transition_seq.get(session_id, -1)
            if payload.transition_seq <= previous_seq:
                raise IngestConflict(f"transition_seq must be greater than {previous_seq}")
            previous_captured = self.last_captured_at.get(session_id)
            if previous_captured and payload.captured_at.isoformat() <= previous_captured:
                raise IngestConflict("captured_at is older than the latest accepted analysis")
            now = utcnow()
            current = CurrentState(
                dog_id=session.dog_id,
                camera_id=camera_id,
                session_id=session_id,
                monitoring_status=payload.monitoring_status,
                activity=payload.activity,
                state=payload.state,
                signals=payload.signals,
                quality=payload.quality,
                analysis=payload.analysis,
                sequence=payload.transition_seq,
                captured_at=payload.captured_at,
                updated_at=now,
            )
            self.current_states[session.dog_id] = current
            camera = self.cameras[camera_id]
            camera_status = (
                MonitoringStatus.analyzing
                if payload.monitoring_status == MonitoringStatus.analyzing
                else payload.monitoring_status
            )
            self.cameras[camera_id] = camera.model_copy(
                update={"status": camera_status, "last_checked_at": now, "updated_at": now}
            )
            open_event = self._find_open_event(session.dog_id)
            active_event: StateEvent | None = open_event
            if payload.monitoring_status != MonitoringStatus.analyzing:
                active_event = self._close_open_event(session.dog_id, payload.captured_at)
            elif current.state:
                if open_event and (payload.transition.changed or open_event.state != current.state.label):
                    self._close_open_event(session.dog_id, payload.captured_at)
                    active_event = self._open_event(current)
                elif open_event:
                    active_event = self._update_open_event(open_event, current)
                else:
                    active_event = self._open_event(current)
            response = IngestResponse(
                accepted=True,
                duplicate=False,
                analysis_id=analysis_id,
                sequence=payload.transition_seq,
                current_state=current,
                event_id=active_event.id if active_event else None,
            )
            self.processed_analyses[analysis_id] = response
            self.last_transition_seq[session_id] = payload.transition_seq
            self.last_captured_at[session_id] = payload.captured_at.isoformat()
            await self._changed()
            return response.model_copy(deep=True)

    async def create_feedback(self, event_id: str, payload: FeedbackCreate) -> Feedback:
        event_id = self.resolve_id(event_id)
        async with self._lock:
            if event_id not in self.events:
                raise StoreNotFound("event")
            feedback = Feedback(id=str(uuid4()), event_id=event_id, created_at=utcnow(), **payload.model_dump())
            self.feedback[feedback.id] = feedback
            await self._changed()
            return feedback.model_copy(deep=True)

    async def find_speech(self, event_id: str, language: str, voice_id: str, model_id: str) -> SpeechAsset | None:
        event_id = self.resolve_id(event_id)
        for speech in self.speeches.values():
            if (
                speech.event_id == event_id
                and speech.language == language
                and speech.voice_id == voice_id
                and speech.model_id == model_id
            ):
                return speech.model_copy(deep=True)
        return None

    async def reserve_speech(self, speech: SpeechAsset) -> SpeechAsset:
        async with self._lock:
            existing = await self.find_speech(speech.event_id, speech.language, speech.voice_id, speech.model_id)
            if existing:
                return existing
            self.speeches[speech.id] = speech
            await self._changed()
            return speech.model_copy(deep=True)

    async def update_speech(self, speech_id: str, **changes: Any) -> SpeechAsset:
        async with self._lock:
            speech = self.speeches.get(speech_id)
            if not speech:
                raise StoreNotFound("speech")
            speech = speech.model_copy(update=changes)
            self.speeches[speech_id] = speech
            await self._changed()
            return speech.model_copy(deep=True)

    async def get_speech(self, speech_id: str) -> SpeechAsset:
        speech = self.speeches.get(speech_id)
        if not speech:
            raise StoreNotFound("speech")
        return speech.model_copy(deep=True)

    async def find_receipt(self, event_id: str, canonical_version: str, network: str) -> Receipt | None:
        event_id = self.resolve_id(event_id)
        for receipt in self.receipts.values():
            if (
                receipt.event_id == event_id
                and receipt.canonical_version == canonical_version
                and receipt.network == network
            ):
                return receipt.model_copy(deep=True)
        return None

    async def reserve_receipt(self, receipt: Receipt) -> Receipt:
        async with self._lock:
            existing = await self.find_receipt(receipt.event_id, receipt.canonical_version, receipt.network)
            if existing:
                return existing
            self.receipts[receipt.id] = receipt
            await self._changed()
            return receipt.model_copy(deep=True)

    async def update_receipt(self, receipt_id: str, **changes: Any) -> Receipt:
        async with self._lock:
            receipt = self.receipts.get(receipt_id)
            if not receipt:
                raise StoreNotFound("receipt")
            receipt = receipt.model_copy(update=changes)
            self.receipts[receipt_id] = receipt
            await self._changed()
            return receipt.model_copy(deep=True)

    async def get_receipt(self, receipt_id: str) -> Receipt:
        receipt = self.receipts.get(receipt_id)
        if not receipt:
            raise StoreNotFound("receipt")
        return receipt.model_copy(deep=True)

    async def list_unsynced_events(self, limit: int = 20) -> list[StateEvent]:
        events = [event for event in self.events.values() if event.ended_at and not event.snowflake_synced_at]
        return [event.model_copy(deep=True) for event in sorted(events, key=lambda item: item.created_at)[:limit]]

    async def mark_event_synced(self, event_id: str) -> None:
        async with self._lock:
            event = self.events.get(event_id)
            if event:
                self.events[event_id] = event.model_copy(update={"snowflake_synced_at": utcnow()})
                await self._changed()

    def export_snapshot(self) -> dict[str, Any]:
        def dump(collection: dict[str, Any]) -> dict[str, Any]:
            return {key: value.model_dump(mode="json") for key, value in collection.items()}

        return {
            "version": 1,
            "dogs": dump(self.dogs),
            "cameras": dump(self.cameras),
            "sessions": dump(self.sessions),
            "current_states": dump(self.current_states),
            "events": dump(self.events),
            "feedback": dump(self.feedback),
            "speeches": dump(self.speeches),
            "receipts": dump(self.receipts),
            "processed_analyses": dump(self.processed_analyses),
            "last_transition_seq": self.last_transition_seq,
            "last_captured_at": self.last_captured_at,
        }

    def import_snapshot(self, payload: dict[str, Any]) -> None:
        mappings = {
            "dogs": (self.dogs, Dog),
            "cameras": (self.cameras, Camera),
            "sessions": (self.sessions, MonitoringSession),
            "current_states": (self.current_states, CurrentState),
            "events": (self.events, StateEvent),
            "feedback": (self.feedback, Feedback),
            "speeches": (self.speeches, SpeechAsset),
            "receipts": (self.receipts, Receipt),
            "processed_analyses": (self.processed_analyses, IngestResponse),
        }
        for name, (target, model) in mappings.items():
            target.clear()
            target.update({key: model.model_validate(value) for key, value in payload.get(name, {}).items()})
        self.last_transition_seq = {key: int(value) for key, value in payload.get("last_transition_seq", {}).items()}
        self.last_captured_at = dict(payload.get("last_captured_at", {}))


class PostgresStore(MemoryStore):
    """Durable single-process repository backed by one atomic JSONB snapshot.

    The deliberately small schema keeps the demo zero-migration and fully
    recoverable. A horizontally scaled deployment should replace this adapter
    with normalized repositories and row-level locks.
    """

    backend = "postgres"

    def __init__(self, settings: Settings, secret_box: SecretBox):
        super().__init__(settings, secret_box)
        self.pool: Any | None = None

    async def initialize(self) -> None:
        try:
            import asyncpg
        except ImportError as exc:
            raise RuntimeError("asyncpg is required for STORE_BACKEND=postgres") from exc
        self.pool = await asyncpg.create_pool(self.settings.postgres_dsn, min_size=1, max_size=4)
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dogsense_api_snapshot (
                    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            row = await connection.fetchrow("SELECT payload FROM dogsense_api_snapshot WHERE singleton = TRUE")
        if row:
            stored = row["payload"]
            self.import_snapshot(json.loads(stored) if isinstance(stored, str) else dict(stored))
        self.ready = True

    async def _changed(self) -> None:
        if not self.pool:
            return
        payload = self.export_snapshot()
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO dogsense_api_snapshot(singleton, payload, updated_at)
                VALUES(TRUE, $1::jsonb, NOW())
                ON CONFLICT(singleton) DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
                """,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )

    async def close(self) -> None:
        self.ready = False
        if self.pool:
            await self.pool.close()
            self.pool = None


def create_store(settings: Settings, secret_box: SecretBox) -> MemoryStore:
    if settings.store_backend == "postgres":
        return PostgresStore(settings, secret_box)
    return MemoryStore(settings, secret_box)
