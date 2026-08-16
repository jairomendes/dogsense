from __future__ import annotations

import pytest

from app.capture import OpenCVFrameSource
from app.config import WorkerSettings


def clear_worker_env(monkeypatch) -> None:
    for name in (
        "APP_ENV",
        "DOGSENSE_AI_PROVIDER",
        "DOGSENSE_INTERNAL_TOKEN",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "DOGSENSE_SESSION_ID",
        "CAMERA_RTSP_URL",
        "MEDIAMTX_RTSP_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_deterministic_and_fake(monkeypatch) -> None:
    clear_worker_env(monkeypatch)
    settings = WorkerSettings.from_env()
    assert settings.ai_provider == "fake"
    assert str(settings.camera_id).endswith("0001")
    assert str(settings.session_id).endswith("0003")
    assert settings.frame_window_seconds == 4.0
    assert settings.frame_sample_fps == 2.0
    assert settings.max_frames == 8


def test_real_provider_requires_key_and_configured_model(monkeypatch) -> None:
    clear_worker_env(monkeypatch)
    monkeypatch.setenv("DOGSENSE_AI_PROVIDER", "gemini")
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        WorkerSettings.from_env()


def test_production_rejects_development_internal_token(monkeypatch) -> None:
    clear_worker_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError, match="DOGSENSE_INTERNAL_TOKEN"):
        WorkerSettings.from_env()


def test_settings_and_source_repr_redact_credentials() -> None:
    canary = "user:super-secret@private-camera"
    settings = WorkerSettings(stream_url=f"rtsp://{canary}/stream", internal_token="token-canary")
    source = OpenCVFrameSource(settings.stream_url)
    assert canary not in repr(settings)
    assert "token-canary" not in repr(settings)
    assert canary not in repr(source)
    assert "<redacted>" in repr(source)
