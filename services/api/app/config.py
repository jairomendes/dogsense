from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


@dataclass(slots=True)
class Settings:
    app_name: str = field(default_factory=lambda: os.getenv("APP_NAME", "DogSense API"))
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development").lower())
    app_host: str = field(default_factory=lambda: os.getenv("APP_HOST", "127.0.0.1"))
    app_base_url: str = field(default_factory=lambda: os.getenv("APP_BASE_URL", "http://localhost:8000"))
    frontend_url: str = field(default_factory=lambda: os.getenv("FRONTEND_URL", "http://localhost:3000"))
    allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: _csv("ALLOWED_ORIGINS", os.getenv("FRONTEND_URL", "http://localhost:3000"))
    )
    demo_mode: bool = field(default_factory=lambda: _bool("DOGSENSE_DEMO_MODE", True))
    store_backend: str = field(default_factory=lambda: os.getenv("STORE_BACKEND", "memory").lower())
    postgres_dsn: str | None = field(
        default_factory=lambda: os.getenv("POSTGRES_DSN") or os.getenv("DATABASE_URL")
    )
    auth_required: bool = field(default_factory=lambda: _bool("AUTH_REQUIRED", False))
    api_token: str = field(default_factory=lambda: os.getenv("DOGSENSE_API_TOKEN", "demo-local-token"))
    internal_api_token: str = field(
        default_factory=lambda: os.getenv("DOGSENSE_INTERNAL_TOKEN")
        or os.getenv("INTERNAL_API_TOKEN", "dogsense-worker-demo-token")
    )
    jwt_secret: str | None = field(default_factory=lambda: os.getenv("JWT_SECRET"))
    credential_encryption_key: str = field(
        default_factory=lambda: os.getenv("CREDENTIAL_ENCRYPTION_KEY", "dogsense-demo-encryption-key-change-me")
    )
    analytics_hmac_key: str = field(
        default_factory=lambda: os.getenv("ANALYTICS_HMAC_KEY", "dogsense-demo-analytics-key-change-me")
    )
    camera_adapter: str = field(default_factory=lambda: os.getenv("CAMERA_ADAPTER", "fake").lower())
    mediamtx_mode: str = field(default_factory=lambda: os.getenv("MEDIAMTX_MODE", "fake").lower())
    mediamtx_api_url: str = field(
        default_factory=lambda: os.getenv("MEDIAMTX_API_URL", "http://mediamtx:9997").rstrip("/")
    )
    snowflake_mode: str = field(default_factory=lambda: os.getenv("SNOWFLAKE_MODE", "fake").lower())
    elevenlabs_mode: str = field(default_factory=lambda: os.getenv("ELEVENLABS_MODE", "fake").lower())
    solana_mode: str = field(default_factory=lambda: os.getenv("SOLANA_MODE", "fake").lower())
    elevenlabs_api_key: str | None = field(default_factory=lambda: os.getenv("ELEVENLABS_API_KEY"))
    elevenlabs_voice_id: str = field(default_factory=lambda: os.getenv("ELEVENLABS_VOICE_ID", "demo-voice"))
    elevenlabs_model_id: str = field(
        default_factory=lambda: os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
    )
    solana_rpc_url: str = field(
        default_factory=lambda: os.getenv("SOLANA_RPC_URL", "https://api.devnet.solana.com")
    )
    solana_network: str = field(default_factory=lambda: os.getenv("SOLANA_NETWORK", "devnet").lower())
    solana_keypair_path: str | None = field(default_factory=lambda: os.getenv("SOLANA_KEYPAIR_PATH"))
    audio_dir: Path = field(
        default_factory=lambda: Path(os.getenv("AUDIO_DIR", str(Path.home() / ".local/share/dogsense/audio")))
    )
    audio_ttl_seconds: int = field(default_factory=lambda: int(os.getenv("AUDIO_TTL_SECONDS", "86400")))
    integration_poll_seconds: float = field(
        default_factory=lambda: float(os.getenv("INTEGRATION_POLL_SECONDS", "5"))
    )
    rate_limit_per_minute: int = field(
        default_factory=lambda: int(os.getenv("SENSITIVE_RATE_LIMIT_PER_MINUTE", "20"))
    )
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    def validate(self) -> Settings:
        if self.store_backend not in {"memory", "postgres"}:
            raise ValueError("STORE_BACKEND must be 'memory' or 'postgres'")
        if self.store_backend == "postgres" and not self.postgres_dsn:
            raise ValueError("POSTGRES_DSN or DATABASE_URL is required for STORE_BACKEND=postgres")
        # ``postgresql+asyncpg://`` is the conventional SQLAlchemy URL used by
        # the deployment manifests. The repository talks to asyncpg directly,
        # whose DSN parser accepts the driver-less PostgreSQL scheme only.
        if self.postgres_dsn and self.postgres_dsn.startswith("postgresql+asyncpg://"):
            self.postgres_dsn = self.postgres_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        for name, value in {
            "CAMERA_ADAPTER": self.camera_adapter,
            "MEDIAMTX_MODE": self.mediamtx_mode,
            "SNOWFLAKE_MODE": self.snowflake_mode,
            "ELEVENLABS_MODE": self.elevenlabs_mode,
            "SOLANA_MODE": self.solana_mode,
        }.items():
            allowed = {"fake", "real"} if name != "CAMERA_ADAPTER" else {"fake", "ffprobe"}
            if value not in allowed:
                raise ValueError(f"{name} must be one of {sorted(allowed)}")
        if self.solana_network != "devnet":
            raise ValueError("The MVP only permits SOLANA_NETWORK=devnet")
        if self.app_env in {"production", "prod"}:
            if self.demo_mode:
                raise ValueError("DOGSENSE_DEMO_MODE cannot be enabled in production")
            if not self.auth_required or not self.jwt_secret:
                raise ValueError("Production requires AUTH_REQUIRED=true and JWT_SECRET")
            if "change-me" in self.credential_encryption_key or "change-me" in self.analytics_hmac_key:
                raise ValueError("Production encryption and HMAC keys must be explicitly configured")
        self.audio_dir = self.audio_dir.expanduser().resolve()
        return self


def load_settings() -> Settings:
    return Settings().validate()
