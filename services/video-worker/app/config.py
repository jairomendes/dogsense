from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return default if raw is None else float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return default if raw is None else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _optional_path(name: str) -> Path | None:
    raw = os.getenv(name)
    return Path(raw).expanduser() if raw else None


@dataclass(frozen=True, slots=True, repr=False)
class WorkerSettings:
    app_env: str = "development"
    stream_url: str = "rtsp://mediamtx:8554/dog-camera"
    api_base_url: str = "http://api:8000"
    internal_token: str = "dogsense-worker-demo-token"
    camera_id: UUID = UUID("00000000-0000-0000-0000-000000000001")
    session_id: UUID = UUID("00000000-0000-0000-0000-000000000003")
    ai_provider: str = "fake"
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    gemini_timeout_seconds: float = 8.0
    gemini_max_retries: int = 1
    prompt_version: str = "behavior-observer-v1"
    schema_version: str = "behavior-analysis-v1"
    frame_window_seconds: float = 4.0
    frame_sample_fps: float = 2.0
    analysis_interval_seconds: float = 2.0
    image_width: int = 640
    image_height: int = 360
    jpeg_quality: int = 75
    buffer_capacity: int = 120
    min_frames: int = 6
    max_frames: int = 8
    duplicate_hamming_distance: int = 3
    stream_freeze_seconds: float = 3.0
    degradation_seconds: float = 10.0
    api_timeout_seconds: float = 5.0
    fake_fixture_path: Path | None = None
    fake_scenario_path: Path | None = None
    health_host: str = "0.0.0.0"
    health_port: int = 8081
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> WorkerSettings:
        stream_url = os.getenv("MEDIAMTX_RTSP_URL") or os.getenv("CAMERA_RTSP_URL")
        settings = cls(
            app_env=os.getenv("APP_ENV", "development"),
            stream_url=stream_url or "rtsp://mediamtx:8554/dog-camera",
            api_base_url=os.getenv("DOGSENSE_API_URL", "http://api:8000"),
            internal_token=os.getenv("DOGSENSE_INTERNAL_TOKEN", "dogsense-worker-demo-token"),
            camera_id=UUID(os.getenv("DOGSENSE_CAMERA_ID", "00000000-0000-0000-0000-000000000001")),
            session_id=UUID(
                os.getenv("DOGSENSE_SESSION_ID", "00000000-0000-0000-0000-000000000003")
            ),
            ai_provider=os.getenv("DOGSENSE_AI_PROVIDER", "fake").lower(),
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
            gemini_model=os.getenv("GEMINI_MODEL"),
            gemini_timeout_seconds=_float("GEMINI_TIMEOUT_SECONDS", 8.0),
            gemini_max_retries=_int("GEMINI_MAX_RETRIES", 1),
            prompt_version=os.getenv("PROMPT_VERSION", "behavior-observer-v1"),
            schema_version=os.getenv("SCHEMA_VERSION", "behavior-analysis-v1"),
            frame_window_seconds=_float("FRAME_WINDOW_SECONDS", 4.0),
            frame_sample_fps=_float("FRAME_SAMPLE_FPS", 2.0),
            analysis_interval_seconds=_float("ANALYSIS_INTERVAL_SECONDS", 2.0),
            image_width=_int("INFERENCE_IMAGE_WIDTH", 640),
            image_height=_int("INFERENCE_IMAGE_HEIGHT", 360),
            jpeg_quality=_int("INFERENCE_JPEG_QUALITY", 75),
            buffer_capacity=_int("FRAME_BUFFER_CAPACITY", 120),
            min_frames=_int("FRAMES_PER_REQUEST_MIN", 6),
            max_frames=_int("FRAMES_PER_REQUEST_MAX", 8),
            duplicate_hamming_distance=_int("FRAME_DUPLICATE_HAMMING_DISTANCE", 3),
            stream_freeze_seconds=_float("STREAM_FREEZE_SECONDS", 3.0),
            degradation_seconds=_float("SERVICE_DEGRADATION_SECONDS", 10.0),
            api_timeout_seconds=_float("DOGSENSE_API_TIMEOUT_SECONDS", 5.0),
            fake_fixture_path=_optional_path("DOGSENSE_FAKE_FIXTURE_PATH"),
            fake_scenario_path=_optional_path("DOGSENSE_FAKE_SCENARIO_PATH"),
            health_host=os.getenv("HEALTH_HOST", "0.0.0.0"),
            health_port=_int("HEALTH_PORT", 8081),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.ai_provider not in {"fake", "gemini"}:
            raise ValueError("DOGSENSE_AI_PROVIDER must be 'fake' or 'gemini'")
        if self.ai_provider == "gemini" and (not self.gemini_api_key or not self.gemini_model):
            raise ValueError("GEMINI_API_KEY and GEMINI_MODEL are required for the Gemini provider")
        if self.prompt_version != "behavior-observer-v1":
            raise ValueError("unsupported PROMPT_VERSION")
        if self.schema_version != "behavior-analysis-v1":
            raise ValueError("unsupported SCHEMA_VERSION")
        positive = {
            "GEMINI_TIMEOUT_SECONDS": self.gemini_timeout_seconds,
            "FRAME_WINDOW_SECONDS": self.frame_window_seconds,
            "FRAME_SAMPLE_FPS": self.frame_sample_fps,
            "ANALYSIS_INTERVAL_SECONDS": self.analysis_interval_seconds,
            "SERVICE_DEGRADATION_SECONDS": self.degradation_seconds,
            "DOGSENSE_API_TIMEOUT_SECONDS": self.api_timeout_seconds,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if not 1 <= self.jpeg_quality <= 100:
            raise ValueError("INFERENCE_JPEG_QUALITY must be between 1 and 100")
        if self.min_frames < 1 or self.max_frames < self.min_frames:
            raise ValueError("frame request bounds are inconsistent")
        if self.buffer_capacity < self.max_frames:
            raise ValueError("FRAME_BUFFER_CAPACITY cannot be smaller than the request")
        if self.gemini_max_retries not in {0, 1}:
            raise ValueError("GEMINI_MAX_RETRIES must be zero or one")
        if (
            self.app_env in {"production", "staging"}
            and self.internal_token == "dogsense-worker-demo-token"
        ):
            raise ValueError("DOGSENSE_INTERNAL_TOKEN must be configured outside development")

    def __repr__(self) -> str:
        return (
            "WorkerSettings("
            f"app_env={self.app_env!r}, stream_url=<redacted>, "
            f"api_base_url={self.api_base_url!r}, internal_token=<redacted>, "
            f"camera_id={self.camera_id!s}, session_id={self.session_id!s}, "
            f"ai_provider={self.ai_provider!r}, gemini_api_key=<redacted>, "
            f"gemini_model={self.gemini_model!r}, prompt_version={self.prompt_version!r})"
        )
