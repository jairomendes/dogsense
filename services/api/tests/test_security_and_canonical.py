from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.canonical import canonical_json, event_snapshot, receipt_memo, snapshot_hash
from app.config import Settings
from app.models import Signal, StateEvent
from app.security import SecretBox, redact_text, validate_rtsp_url


def test_canonicalization_golden_vector():
    event = StateEvent(
        id="cf72fd61-54f0-4f63-a9e6-52b38900d23c",
        dog_id="35f31931-9fe0-47df-95e2-01cf8403ee31",
        camera_id="camera",
        session_id="session",
        started_at=datetime(2026, 8, 15, 15, 20, tzinfo=UTC),
        ended_at=datetime(2026, 8, 15, 15, 20, 18, tzinfo=UTC),
        activity="pacing",
        state="stress_signals",
        confidence_avg=0.8100004,
        confidence_max=0.9,
        observation_quality_avg=0.8,
        signals=[Signal(name="repetitive_movement", confidence=0.9), Signal(name="lowered_posture", confidence=0.8)],
        prompt_version="behavior-observer-v1",
        model_name="model",
        created_at=datetime(2026, 8, 15, 15, 20, tzinfo=UTC),
    )
    snapshot = event_snapshot(event, "test-key")
    encoded = canonical_json(snapshot)
    assert encoded == (
        b'{"activity":"pacing","canonical_version":"dogsense-event-v1","confidence_avg":0.81,'
        b'"dog_id_hash":"14b7641cde","ended_at":"2026-08-15T15:20:18.000Z",'
        b'"event_id":"cf72fd61-54f0-4f63-a9e6-52b38900d23c","prompt_version":"behavior-observer-v1",'
        b'"signals":["lowered_posture","repetitive_movement"],"started_at":"2026-08-15T15:20:00.000Z",'
        b'"state":"stress_signals"}'
    )
    digest = snapshot_hash(snapshot)
    assert len(digest) == 64
    assert receipt_memo(event.id, digest).endswith(digest)


def test_secret_box_redaction_and_rtsp_validation():
    box = SecretBox("a-key")
    encrypted = box.encrypt({"password": "canary-secret", "rtsp_url": "rtsp://user:canary-secret@host/path"})
    assert "canary-secret" not in encrypted
    assert box.decrypt(encrypted)["password"] == "canary-secret"
    assert "canary-secret" not in redact_text("rtsp://user:canary-secret@host/path")
    with pytest.raises(ValueError):
        validate_rtsp_url("https://example.com/video")
    with pytest.raises(ValueError):
        validate_rtsp_url("rtsp://127.0.0.1/camera", allow_private=False)


def test_production_settings_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        Settings(app_env="production", demo_mode=True, audio_dir=tmp_path).validate()


def test_settings_normalize_sqlalchemy_asyncpg_dsn(tmp_path):
    settings = Settings(
        app_env="test",
        store_backend="postgres",
        postgres_dsn="postgresql+asyncpg://user:secret@postgres:5432/dogsense",
        audio_dir=tmp_path,
    ).validate()

    assert settings.postgres_dsn == "postgresql://user:secret@postgres:5432/dogsense"
