from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from app.config import Settings
from app.integrations import IntegrationBundle
from app.integrations.adapters import AudioStorage
from app.models import BehavioralState, IntegrationReport, StateEvent, utcnow
from app.repositories import MemoryStore
from app.security import RateLimiter, SecretBox

logger = logging.getLogger("dogsense.api")


class WebSocketHub:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, dog_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[dog_id].add(websocket)

    async def disconnect(self, dog_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections[dog_id].discard(websocket)
            if not self._connections[dog_id]:
                self._connections.pop(dog_id, None)

    async def publish(self, dog_id: str, message: dict[str, Any]) -> None:
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"), default=str)
        async with self._lock:
            targets = tuple(self._connections.get(dog_id, ()))
        stale: list[WebSocket] = []
        for websocket in targets:
            try:
                await websocket.send_text(encoded)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            await self.disconnect(dog_id, websocket)


@dataclass(slots=True)
class AppContext:
    settings: Settings
    secret_box: SecretBox
    store: MemoryStore
    integrations: IntegrationBundle
    audio: AudioStorage
    hub: WebSocketHub = field(default_factory=WebSocketHub)
    sensitive_limiter: RateLimiter = field(init=False)
    speech_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    receipt_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    background_tasks: list[asyncio.Task[Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.sensitive_limiter = RateLimiter(self.settings.rate_limit_per_minute)


def speech_text(event: StateEvent, dog_name: str, language: str) -> str:
    duration = max(1, event.duration_seconds)
    signals = ", ".join(signal.name.replace("_", " ") for signal in event.signals[:3]) or "limited visible signals"
    state = getattr(event.state, "value", event.state)
    activity = getattr(event.activity, "value", event.activity).replace("_", " ")
    confidence = f"{round(event.confidence_avg * 100)} percent"
    if language == "pt-BR":
        if state == BehavioralState.stress_signals:
            return (
                f"Alerta DogSense. {dog_name} apresentou sinais persistentes associados a estresse por "
                f"{duration} segundos. Os principais sinais observados são {signals}. Verifique a câmera ao vivo."
            )
        if state == BehavioralState.alert:
            return (
                f"Atualização DogSense. {dog_name} parece em alerta. Os principais sinais observados são "
                f"{signals}. Verifique a câmera ao vivo para obter contexto."
            )
        return (
            f"Atualização DogSense. O estado provável de {dog_name} é {state.replace('_', ' ')}, com atividade "
            f"{activity}, por {duration} segundos. A confiança é {confidence}."
        )
    if state == BehavioralState.stress_signals:
        return (
            f"DogSense alert. {dog_name} has shown persistent stress-related signals for {duration} seconds. "
            f"The main observed signals are {signals}. Please check the live camera."
        )
    if state == BehavioralState.alert:
        return (
            f"DogSense update. {dog_name} appears alert. The main observed signals are {signals}. "
            "Please check the live camera for context."
        )
    return (
        f"DogSense update. {dog_name} likely appears {state.replace('_', ' ')} and has been {activity} for "
        f"{duration} seconds. Confidence is {confidence}."
    )


def live_message(current: Any) -> dict[str, Any]:
    timestamp = utcnow().isoformat().replace("+00:00", "Z")
    if current.monitoring_status == "analyzing":
        return {
            "type": "live_state_updated",
            "timestamp": timestamp,
            **current.model_dump(mode="json"),
        }
    return {
        "type": "monitoring_status_updated",
        "timestamp": timestamp,
        "dog_id": current.dog_id,
        "status": current.monitoring_status,
        "sequence": current.sequence,
    }


async def integration_worker(context: AppContext) -> None:
    while True:
        try:
            for event in await context.store.list_unsynced_events(limit=20):
                try:
                    await context.integrations.analytics.sync_event(event)
                except Exception:
                    logger.warning("snowflake_sync_failed", extra={"event_id": event.id})
                    break
                await context.store.mark_event_synced(event.id)
            await cleanup_expired_audio(context)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("integration_worker_failed")
        await asyncio.sleep(context.settings.integration_poll_seconds)


async def cleanup_expired_audio(context: AppContext) -> None:
    now = utcnow()
    for speech in tuple(context.store.speeches.values()):
        if speech.expires_at <= now:
            await context.audio.delete(speech.id)


async def integration_reports(context: AppContext) -> list[IntegrationReport]:
    async def guarded(call: Any, name: str, mode: str) -> IntegrationReport:
        try:
            return await asyncio.wait_for(call(), timeout=4.0)
        except Exception:
            return IntegrationReport(
                name=name,
                mode=mode,
                status="degraded",
                configured=True,
                detail="health check failed",
            )

    return list(
        await asyncio.gather(
            guarded(context.integrations.analytics.health, "snowflake", context.settings.snowflake_mode),
            guarded(context.integrations.speech.health, "elevenlabs", context.settings.elevenlabs_mode),
            guarded(context.integrations.ledger.health, "solana", context.settings.solana_mode),
        )
    )


def analytics_summary(events: list[StateEvent]) -> dict[str, Any]:
    durations: dict[str, int] = defaultdict(int)
    for event in events:
        durations[getattr(event.state, "value", event.state)] += event.duration_seconds
    dominant = max(durations, key=durations.get) if durations else None
    return {
        "event_count": len(events),
        "total_duration_seconds": sum(durations.values()),
        "dominant_state": dominant,
        "average_confidence": (
            round(sum(event.confidence_avg for event in events) / len(events), 6) if events else None
        ),
        "generated_at": utcnow(),
    }


def analytics_distribution(events: list[StateEvent]) -> dict[str, Any]:
    durations: dict[str, int] = defaultdict(int)
    for event in events:
        durations[getattr(event.state, "value", event.state)] += event.duration_seconds
    total = sum(durations.values())
    return {
        "total_duration_seconds": total,
        "states": [
            {
                "state": state,
                "duration_seconds": duration,
                "percentage": round(duration / total * 100, 2) if total else 0,
            }
            for state, duration in sorted(durations.items())
        ],
    }
