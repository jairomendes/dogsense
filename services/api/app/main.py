from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.canonical import (
    CANONICAL_VERSION,
    canonical_json,
    event_snapshot,
    hash_from_memo,
    receipt_memo,
    snapshot_hash,
)
from app.config import Settings, load_settings
from app.integrations import build_integrations
from app.integrations.adapters import AudioStorage, _credential_url
from app.models import (
    AnalysisIngest,
    BehavioralState,
    CameraCreate,
    CameraHealth,
    CameraPublic,
    CameraTestResult,
    CameraUpdate,
    DemoBootstrap,
    Dog,
    DogCreate,
    DogUpdate,
    EventPublic,
    Feedback,
    FeedbackCreate,
    IngestResponse,
    IntegrationReport,
    MonitoringSession,
    Receipt,
    ReceiptVerification,
    SessionCreate,
    SpeechAsset,
    SpeechCreate,
    StateEvent,
    utcnow,
)
from app.repositories import (
    DEMO_CAMERA_ID,
    DEMO_DOG_ID,
    DEMO_SESSION_ID,
    IngestConflict,
    StoreNotFound,
    create_store,
)
from app.security import (
    SecretBox,
    redact_text,
    require_auth,
    require_internal_auth,
    validate_rtsp_url,
    websocket_authorized,
)
from app.services import (
    AppContext,
    analytics_distribution,
    analytics_summary,
    integration_reports,
    integration_worker,
    live_message,
    speech_text,
)

logger = logging.getLogger("dogsense.api")
_CORRELATION_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "service": "dogsense-api",
            "event": redact_text(record.getMessage()),
        }
        for key in ("request_id", "camera_id", "session_id", "analysis_id", "event_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def _context(request: Request) -> AppContext:
    return request.app.state.context


def _not_found(exc: StoreNotFound) -> HTTPException:
    resource = exc.args[0] if exc.args else "resource"
    return HTTPException(status_code=404, detail=f"{resource} not found")


def _camera_url(settings: Settings, value: str) -> str:
    try:
        return validate_rtsp_url(value, allow_private=settings.app_env not in {"production", "prod"})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _event_public(event: StateEvent) -> EventPublic:
    return EventPublic.from_record(event)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = (settings or load_settings()).validate()
    configure_logging(settings.log_level)
    secret_box = SecretBox(settings.credential_encryption_key)
    store = create_store(settings, secret_box)
    context = AppContext(
        settings=settings,
        secret_box=secret_box,
        store=store,
        integrations=build_integrations(settings),
        audio=AudioStorage(settings.audio_dir),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await context.store.initialize()
        await context.audio.initialize()
        if settings.demo_mode:
            await context.store.seed_demo()
        task = asyncio.create_task(integration_worker(context), name="dogsense-integration-worker")
        context.background_tasks.append(task)
        try:
            yield
        finally:
            for background in context.background_tasks:
                background.cancel()
            for background in context.background_tasks:
                with suppress(asyncio.CancelledError):
                    await background
            await context.store.close()

    app = FastAPI(
        title="DogSense API",
        version=__version__,
        description=(
            "Operational API for probable canine behavior monitoring. "
            "It does not provide veterinary diagnoses."
        ),
        lifespan=lifespan,
    )
    app.state.context = context
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID", "X-Internal-Token"],
    )

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next: Any) -> Response:
        supplied = request.headers.get("x-correlation-id", "")
        request_id = supplied if _CORRELATION_RE.fullmatch(supplied) else str(uuid4())
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request_failed", extra={"request_id": request_id})
            raise
        response.headers["X-Correlation-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(StoreNotFound)
    async def store_not_found_handler(_: Request, exc: StoreNotFound) -> JSONResponse:
        resource = exc.args[0] if exc.args else "resource"
        return JSONResponse(status_code=404, content={"detail": f"{resource} not found"})

    @app.exception_handler(IngestConflict)
    async def ingest_conflict_handler(_: Request, exc: IngestConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health/live", tags=["operation"])
    async def health_live() -> dict[str, Any]:
        return {"status": "ok", "service": "dogsense-api", "version": __version__, "timestamp": utcnow()}

    @app.get("/health/ready", tags=["operation"])
    async def health_ready(request: Request) -> Response:
        ctx = _context(request)
        reports = await integration_reports(ctx)
        payload = {
            "status": "ready" if ctx.store.ready else "not_ready",
            "store": {"backend": ctx.store.backend, "ready": ctx.store.ready},
            "integrations": [report.model_dump(mode="json") for report in reports],
            "timestamp": utcnow().isoformat().replace("+00:00", "Z"),
        }
        return JSONResponse(status_code=200 if ctx.store.ready else 503, content=payload)

    @app.get("/api/v1/integrations/status", response_model=list[IntegrationReport], tags=["operation"])
    async def integrations_status(request: Request, _: None = Depends(require_auth)) -> list[IntegrationReport]:
        return await integration_reports(_context(request))

    @app.get("/api/v1/demo/bootstrap", response_model=DemoBootstrap, tags=["demo"])
    async def demo_bootstrap(request: Request) -> DemoBootstrap:
        ctx = _context(request)
        if not ctx.settings.demo_mode:
            raise HTTPException(status_code=404, detail="demo mode is disabled")
        events = await ctx.store.list_events(DEMO_DOG_ID, limit=1)
        return DemoBootstrap(
            mode="demo",
            dog_id=DEMO_DOG_ID,
            camera_id=DEMO_CAMERA_ID,
            session_id=DEMO_SESSION_ID,
            current_event_id=events[0].id if events else None,
            api_token=None,
        )

    @app.post("/api/v1/dogs", response_model=Dog, status_code=201, tags=["dogs"])
    async def create_dog(payload: DogCreate, request: Request, _: None = Depends(require_auth)) -> Dog:
        return await _context(request).store.create_dog(payload.name, payload.timezone)

    @app.get("/api/v1/dogs/{dog_id}", response_model=Dog, tags=["dogs"])
    async def get_dog(dog_id: str, request: Request, _: None = Depends(require_auth)) -> Dog:
        return await _context(request).store.get_dog(dog_id)

    @app.patch("/api/v1/dogs/{dog_id}", response_model=Dog, tags=["dogs"])
    async def update_dog(
        dog_id: str, payload: DogUpdate, request: Request, _: None = Depends(require_auth)
    ) -> Dog:
        changes = payload.model_dump(exclude_unset=True)
        if "name" in changes and changes["name"]:
            changes["name"] = " ".join(changes["name"].strip().split())
        return await _context(request).store.update_dog(dog_id, changes)

    @app.post("/api/v1/cameras", response_model=CameraPublic, status_code=201, tags=["cameras"])
    async def create_camera(
        payload: CameraCreate, request: Request, _: None = Depends(require_auth)
    ) -> CameraPublic:
        ctx = _context(request)
        camera = await ctx.store.create_camera(
            dog_id=payload.dog_id,
            name=payload.name,
            rtsp_url=_camera_url(ctx.settings, payload.rtsp_url),
            username=payload.username,
            password=payload.password,
            active=payload.active,
        )
        return CameraPublic.from_record(camera)

    @app.get("/api/v1/cameras/{camera_id}", response_model=CameraPublic, tags=["cameras"])
    async def get_camera(camera_id: str, request: Request, _: None = Depends(require_auth)) -> CameraPublic:
        return CameraPublic.from_record(await _context(request).store.get_camera(camera_id))

    @app.patch("/api/v1/cameras/{camera_id}", response_model=CameraPublic, tags=["cameras"])
    async def update_camera(
        camera_id: str, payload: CameraUpdate, request: Request, _: None = Depends(require_auth)
    ) -> CameraPublic:
        ctx = _context(request)
        changes = payload.model_dump(exclude_unset=True)
        if changes.get("rtsp_url"):
            changes["rtsp_url"] = _camera_url(ctx.settings, changes["rtsp_url"])
        return CameraPublic.from_record(await ctx.store.update_camera(camera_id, changes))

    @app.post("/api/v1/cameras/{camera_id}/test", response_model=CameraTestResult, tags=["cameras"])
    async def test_camera(
        camera_id: str, request: Request, _: None = Depends(require_auth)
    ) -> CameraTestResult:
        ctx = _context(request)
        camera = await ctx.store.get_camera(camera_id)
        credentials = ctx.secret_box.decrypt(camera.encrypted_credentials)
        result = await ctx.integrations.camera.probe(
            credentials["rtsp_url"], credentials.get("username"), credentials.get("password")
        )
        result = result.model_copy(update={"camera_id": camera.id})
        await ctx.store.set_camera_check(camera.id, result.success)
        return result

    @app.get("/api/v1/cameras/{camera_id}/health", response_model=CameraHealth, tags=["cameras"])
    async def camera_health(camera_id: str, request: Request, _: None = Depends(require_auth)) -> CameraHealth:
        camera = await _context(request).store.get_camera(camera_id)
        return CameraHealth(
            camera_id=camera.id,
            status=camera.status,
            online=camera.status == "analyzing",
            last_checked_at=camera.last_checked_at,
        )

    @app.post("/api/v1/monitoring/sessions", response_model=MonitoringSession, status_code=201, tags=["monitoring"])
    async def start_monitoring(
        payload: SessionCreate, request: Request, _: None = Depends(require_auth)
    ) -> MonitoringSession:
        ctx = _context(request)
        dog = await ctx.store.get_dog(payload.dog_id)
        camera = await ctx.store.get_camera(payload.camera_id)
        if camera.dog_id != dog.id:
            raise HTTPException(status_code=422, detail="camera does not belong to dog")
        credentials = ctx.secret_box.decrypt(camera.encrypted_credentials)
        source_url = _credential_url(
            credentials["rtsp_url"], credentials.get("username"), credentials.get("password")
        )
        if camera.id != DEMO_CAMERA_ID:
            try:
                await ctx.integrations.stream.publish("dog-camera", source_url)
            except Exception as exc:
                logger.warning("stream_publish_failed", extra={"camera_id": camera.id})
                raise HTTPException(status_code=503, detail="stream service is unavailable") from exc
        return await ctx.store.start_session(dog.id, camera.id)

    @app.get(
        "/api/v1/monitoring/sessions/{session_id}", response_model=MonitoringSession, tags=["monitoring"]
    )
    async def get_monitoring(
        session_id: str, request: Request, _: None = Depends(require_auth)
    ) -> MonitoringSession:
        return await _context(request).store.get_session(session_id)

    @app.delete(
        "/api/v1/monitoring/sessions/{session_id}", response_model=MonitoringSession, tags=["monitoring"]
    )
    async def stop_monitoring(
        session_id: str, request: Request, _: None = Depends(require_auth)
    ) -> MonitoringSession:
        ctx = _context(request)
        session = await ctx.store.stop_session(session_id)
        if session.camera_id != DEMO_CAMERA_ID:
            with suppress(Exception):
                await ctx.integrations.stream.remove("dog-camera")
        return session

    @app.get("/api/v1/dogs/{dog_id}/state/current", tags=["monitoring"])
    async def current_state(dog_id: str, request: Request, _: None = Depends(require_auth)) -> Any:
        return await _context(request).store.get_current_state(dog_id)

    @app.get("/api/v1/dogs/{dog_id}/events", response_model=list[EventPublic], tags=["events"])
    async def list_events(
        dog_id: str,
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
        before: datetime | None = Query(default=None),
        event_state: BehavioralState | None = Query(default=None, alias="state"),
        _: None = Depends(require_auth),
    ) -> list[EventPublic]:
        if before and before.tzinfo is None:
            raise HTTPException(status_code=422, detail="before must include a timezone")
        records = await _context(request).store.list_events(
            dog_id,
            limit=limit,
            state=event_state,
            before=before.astimezone(UTC) if before else None,
        )
        return [_event_public(event) for event in records]

    @app.get("/api/v1/events/{event_id}", response_model=EventPublic, tags=["events"])
    async def get_event(event_id: str, request: Request, _: None = Depends(require_auth)) -> EventPublic:
        return _event_public(await _context(request).store.get_event(event_id))

    @app.post("/api/v1/events/{event_id}/feedback", response_model=Feedback, status_code=201, tags=["events"])
    async def create_feedback(
        event_id: str, payload: FeedbackCreate, request: Request, _: None = Depends(require_auth)
    ) -> Feedback:
        return await _context(request).store.create_feedback(event_id, payload)

    @app.post("/api/v1/events/{event_id}/speech", response_model=SpeechAsset, status_code=201, tags=["speech"])
    async def create_speech(
        event_id: str,
        payload: SpeechCreate,
        request: Request,
        response: Response,
        _: None = Depends(require_auth),
    ) -> SpeechAsset:
        ctx = _context(request)
        ctx.sensitive_limiter.check(f"speech:{event_id}")
        async with ctx.speech_lock:
            event = await ctx.store.get_event(event_id)
            existing = await ctx.store.find_speech(
                event.id, payload.language, ctx.settings.elevenlabs_voice_id, ctx.settings.elevenlabs_model_id
            )
            if existing:
                response.status_code = 200
                return existing
            dog = await ctx.store.get_dog(event.dog_id)
            text = speech_text(event, dog.name, payload.language)
            now = utcnow()
            speech = SpeechAsset(
                id=str(uuid4()),
                event_id=event.id,
                language=payload.language,
                text=text,
                voice_id=ctx.settings.elevenlabs_voice_id,
                model_id=ctx.settings.elevenlabs_model_id,
                status="pending",
                expires_at=now + timedelta(seconds=ctx.settings.audio_ttl_seconds),
                created_at=now,
            )
            await ctx.store.reserve_speech(speech)
            try:
                audio, mime_type = await ctx.integrations.speech.synthesize(text, payload.language)
                await ctx.audio.write(speech.id, audio)
                return await ctx.store.update_speech(speech.id, status="ready", mime_type=mime_type)
            except Exception:
                logger.warning("speech_generation_failed", extra={"event_id": event.id})
                response.status_code = 202
                return await ctx.store.update_speech(
                    speech.id, status="failed", error="speech provider unavailable"
                )

    @app.get("/api/v1/speech/{speech_id}", response_model=SpeechAsset, tags=["speech"])
    async def get_speech(speech_id: str, request: Request, _: None = Depends(require_auth)) -> SpeechAsset:
        return await _context(request).store.get_speech(speech_id)

    @app.get("/api/v1/speech/{speech_id}/audio", tags=["speech"])
    async def get_speech_audio(speech_id: str, request: Request, _: None = Depends(require_auth)) -> Response:
        ctx = _context(request)
        speech = await ctx.store.get_speech(speech_id)
        if speech.expires_at <= utcnow():
            await ctx.audio.delete(speech.id)
            raise HTTPException(status_code=410, detail="audio asset expired")
        if speech.status != "ready":
            raise HTTPException(status_code=409, detail=f"audio is {speech.status}")
        try:
            content = await ctx.audio.read(speech.id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=410, detail="audio asset is no longer available") from exc
        return Response(
            content=content,
            media_type=speech.mime_type,
            headers={"Cache-Control": "private, max-age=300", "Content-Disposition": "inline"},
        )

    @app.post("/api/v1/events/{event_id}/receipt", response_model=Receipt, status_code=201, tags=["receipts"])
    async def create_receipt(
        event_id: str,
        request: Request,
        response: Response,
        _: None = Depends(require_auth),
    ) -> Receipt:
        ctx = _context(request)
        ctx.sensitive_limiter.check(f"receipt:{event_id}")
        async with ctx.receipt_lock:
            event = await ctx.store.get_event(event_id)
            if event.ended_at is None:
                raise HTTPException(status_code=409, detail="receipt requires an ended event")
            snapshot = event_snapshot(event, ctx.settings.analytics_hmac_key)
            event_hash = snapshot_hash(snapshot)
            existing = await ctx.store.find_receipt(event.id, CANONICAL_VERSION, "devnet")
            if existing and existing.status == "confirmed":
                response.status_code = 200
                return existing
            receipt = existing or Receipt(
                id=str(uuid4()),
                event_id=event.id,
                network="devnet",
                canonical_version=CANONICAL_VERSION,
                canonical_snapshot=snapshot,
                event_hash=event_hash,
                memo=receipt_memo(event.id, event_hash),
                transaction_signature=None,
                status="pending",
                confirmed_at=None,
                verification_status="pending",
                created_at=utcnow(),
            )
            if not existing:
                await ctx.store.reserve_receipt(receipt)
            else:
                response.status_code = 200
            try:
                signature, confirmed = await ctx.integrations.ledger.publish_memo(receipt.memo)
                receipt = await ctx.store.update_receipt(
                    receipt.id,
                    transaction_signature=signature,
                    status="confirmed" if confirmed else "pending",
                    confirmed_at=utcnow() if confirmed else None,
                    error=None,
                )
            except Exception:
                logger.warning("receipt_publish_failed", extra={"event_id": event.id})
                response.status_code = 202
                receipt = await ctx.store.update_receipt(
                    receipt.id, status="pending", error="Solana Devnet is temporarily unavailable"
                )
            await ctx.hub.publish(
                event.dog_id,
                {
                    "type": "receipt_created",
                    "event_id": event.id,
                    "network": receipt.network,
                    "transaction_signature": receipt.transaction_signature,
                    "event_hash": receipt.event_hash,
                    "status": receipt.status,
                },
            )
            return receipt

    @app.get("/api/v1/receipts/{receipt_id}", response_model=Receipt, tags=["receipts"])
    async def get_receipt(receipt_id: str, request: Request, _: None = Depends(require_auth)) -> Receipt:
        return await _context(request).store.get_receipt(receipt_id)

    @app.post(
        "/api/v1/receipts/{receipt_id}/verify", response_model=ReceiptVerification, tags=["receipts"]
    )
    async def verify_receipt(
        receipt_id: str, request: Request, _: None = Depends(require_auth)
    ) -> ReceiptVerification:
        ctx = _context(request)
        receipt = await ctx.store.get_receipt(receipt_id)
        event = await ctx.store.get_event(receipt.event_id)
        try:
            current_snapshot = event_snapshot(event, ctx.settings.analytics_hmac_key)
            current_hash = snapshot_hash(current_snapshot)
            snapshot_matches = canonical_json(current_snapshot) == canonical_json(receipt.canonical_snapshot)
        except ValueError:
            current_hash, snapshot_matches = snapshot_hash(receipt.canonical_snapshot), False
        memo = None
        if receipt.transaction_signature:
            try:
                memo = await ctx.integrations.ledger.fetch_memo(receipt.transaction_signature)
            except Exception:
                logger.warning("receipt_verification_lookup_failed", extra={"event_id": event.id})
        if memo is None and ctx.settings.solana_mode == "fake":
            memo = receipt.memo
        on_chain_hash = hash_from_memo(memo)
        verified = bool(
            receipt.status == "confirmed"
            and snapshot_matches
            and current_hash == receipt.event_hash
            and on_chain_hash == receipt.event_hash
        )
        await ctx.store.update_receipt(
            receipt.id, verification_status="verified" if verified else ("pending" if not on_chain_hash else "mismatch")
        )
        return ReceiptVerification(
            verified=verified,
            local_hash=current_hash,
            on_chain_hash=on_chain_hash,
            transaction_status=receipt.status,
            network="devnet",
            snapshot_matches_event=snapshot_matches,
        )

    async def _dog_events(
        ctx: AppContext,
        dog_id: str,
        start: datetime | None,
        end: datetime | None,
    ) -> list[StateEvent]:
        events = await ctx.store.list_events(dog_id, limit=200)
        if start:
            if start.tzinfo is None:
                raise HTTPException(status_code=422, detail="start must include a timezone")
            events = [event for event in events if event.started_at >= start.astimezone(UTC)]
        if end:
            if end.tzinfo is None:
                raise HTTPException(status_code=422, detail="end must include a timezone")
            events = [event for event in events if event.started_at <= end.astimezone(UTC)]
        return events

    @app.get("/api/v1/dogs/{dog_id}/analytics/summary", tags=["analytics"])
    async def summary(
        dog_id: str,
        request: Request,
        start: datetime | None = None,
        end: datetime | None = None,
        _: None = Depends(require_auth),
    ) -> dict[str, Any]:
        events = await _dog_events(_context(request), dog_id, start, end)
        return {"dog_id": _context(request).store.resolve_id(dog_id), **analytics_summary(events)}

    @app.get("/api/v1/dogs/{dog_id}/analytics/timeline", response_model=list[EventPublic], tags=["analytics"])
    async def timeline(
        dog_id: str,
        request: Request,
        start: datetime | None = None,
        end: datetime | None = None,
        _: None = Depends(require_auth),
    ) -> list[EventPublic]:
        return [_event_public(event) for event in await _dog_events(_context(request), dog_id, start, end)]

    @app.get("/api/v1/dogs/{dog_id}/analytics/distribution", tags=["analytics"])
    async def distribution(
        dog_id: str,
        request: Request,
        start: datetime | None = None,
        end: datetime | None = None,
        _: None = Depends(require_auth),
    ) -> dict[str, Any]:
        events = await _dog_events(_context(request), dog_id, start, end)
        return {"dog_id": _context(request).store.resolve_id(dog_id), **analytics_distribution(events)}

    @app.post(
        "/api/v1/internal/analyses",
        response_model=IngestResponse,
        dependencies=[Depends(require_internal_auth)],
        tags=["internal"],
    )
    @app.post(
        "/internal/v1/analyses",
        response_model=IngestResponse,
        include_in_schema=False,
        dependencies=[Depends(require_internal_auth)],
    )
    async def ingest_analysis(payload: AnalysisIngest, request: Request) -> IngestResponse:
        ctx = _context(request)
        result = await ctx.store.ingest(payload)
        if result.accepted:
            await ctx.hub.publish(result.current_state.dog_id, live_message(result.current_state))
        return result

    @app.websocket("/api/v1/live/dogs/{dog_id}")
    async def dog_live(websocket: WebSocket, dog_id: str) -> None:
        ctx: AppContext = websocket.app.state.context
        origin = websocket.headers.get("origin")
        if origin and origin not in ctx.settings.allowed_origins:
            await websocket.close(code=1008, reason="origin not allowed")
            return
        if not await websocket_authorized(websocket, ctx.settings):
            await websocket.close(code=4401, reason="authentication required")
            return
        resolved = ctx.store.resolve_id(dog_id)
        try:
            await ctx.store.get_dog(resolved)
        except StoreNotFound:
            await websocket.close(code=4404, reason="dog not found")
            return
        await ctx.hub.connect(resolved, websocket)
        try:
            try:
                current = await ctx.store.get_current_state(resolved)
            except StoreNotFound:
                current = None
            recent = await ctx.store.list_events(resolved, limit=20)
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "timestamp": utcnow().isoformat().replace("+00:00", "Z"),
                    "sequence": current.sequence if current else 0,
                    "current_state": current.model_dump(mode="json") if current else None,
                    "recent_events": [_event_public(event).model_dump(mode="json") for event in recent],
                }
            )
            while True:
                try:
                    message = await asyncio.wait_for(websocket.receive_text(), timeout=15.0)
                    if message.strip().lower() == "ping":
                        await websocket.send_json({"type": "pong", "timestamp": utcnow().isoformat()})
                except TimeoutError:
                    await websocket.send_json(
                        {"type": "heartbeat", "timestamp": utcnow().isoformat().replace("+00:00", "Z")}
                    )
        except WebSocketDisconnect:
            pass
        finally:
            await ctx.hub.disconnect(resolved, websocket)

    return app


app = create_app()
