from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        demo_mode=True,
        store_backend="memory",
        auth_required=False,
        internal_api_token="test-worker-token",
        credential_encryption_key="test-encryption-key",
        analytics_hmac_key="test-analytics-key",
        camera_adapter="fake",
        mediamtx_mode="fake",
        snowflake_mode="fake",
        elevenlabs_mode="fake",
        solana_mode="fake",
        audio_dir=tmp_path / "audio",
        integration_poll_seconds=3600,
    ).validate()


@pytest.fixture
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
