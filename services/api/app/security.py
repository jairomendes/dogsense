from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings

_SECRET_RE = re.compile(
    r"(?i)(password|token|api[_-]?key|secret|authorization)\s*[:=]\s*([^\s,;]+)"
)
_bearer = HTTPBearer(auto_error=False)


def redact_text(value: str) -> str:
    value = re.sub(r"(?i)(rtsp[s]?://)([^/@\s]+)@", r"\1***:***@", value)
    return _SECRET_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)


def redact_rtsp_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or "invalid-host"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme.lower(), f"{host}{port}", "/***", "", ""))


def validate_rtsp_url(value: str, *, allow_private: bool = True) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"rtsp", "rtsps"}:
        raise ValueError("camera URL must use rtsp:// or rtsps://")
    if not parsed.hostname:
        raise ValueError("camera URL must include a host")
    if not allow_private:
        lowered = parsed.hostname.lower()
        if lowered in {"localhost", "localhost.localdomain"}:
            raise ValueError("localhost camera URLs are not allowed in public mode")
        try:
            address = ipaddress.ip_address(lowered)
        except ValueError:
            address = None
        if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
            raise ValueError("private camera addresses are not allowed in public mode")
    return value


class SecretBox:
    def __init__(self, secret: str):
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._fernet = Fernet(key)

    def encrypt(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(raw).decode("ascii")

    def decrypt(self, token: str) -> dict[str, Any]:
        try:
            raw = self._fernet.decrypt(token.encode("ascii"))
        except InvalidToken as exc:
            raise ValueError("camera credential ciphertext is invalid") from exc
        return json.loads(raw)


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_jwt(token: str, secret: str) -> dict[str, Any]:
    try:
        header_raw, payload_raw, signature_raw = token.split(".")
        header = json.loads(_b64url_decode(header_raw))
        payload = json.loads(_b64url_decode(payload_raw))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JWT") from exc
    if header.get("alg") != "HS256":
        raise ValueError("only HS256 JWTs are accepted")
    expected = hmac.new(secret.encode(), f"{header_raw}.{payload_raw}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64url_decode(signature_raw)):
        raise ValueError("invalid JWT signature")
    if float(payload.get("exp", 0)) <= time.time():
        raise ValueError("JWT expired")
    return payload


def token_is_valid(token: str | None, settings: Settings) -> bool:
    if not settings.auth_required:
        return True
    if not token:
        return False
    if settings.app_env not in {"production", "prod"} and hmac.compare_digest(token, settings.api_token):
        return True
    if settings.jwt_secret:
        try:
            validate_jwt(token, settings.jwt_secret)
            return True
        except ValueError:
            return False
    return False


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    settings: Settings = request.app.state.context.settings
    token = credentials.credentials if credentials else None
    if not token_is_valid(token, settings):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")


async def require_internal_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    settings: Settings = request.app.state.context.settings
    supplied = request.headers.get("x-internal-token") or (credentials.credentials if credentials else None)
    if not supplied or not hmac.compare_digest(supplied, settings.internal_api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid internal token")


async def websocket_authorized(websocket: WebSocket, settings: Settings) -> bool:
    token = websocket.query_params.get("token")
    if not token:
        authorization = websocket.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            token = authorization[7:]
    return token_is_valid(token, settings)


@dataclass(slots=True)
class RateLimiter:
    maximum: int
    period_seconds: int = 60
    _calls: dict[str, deque[float]] = field(init=False, default_factory=lambda: defaultdict(deque))

    def __post_init__(self) -> None:
        if self.maximum < 1:
            raise ValueError("rate limit maximum must be positive")

    def check(self, key: str) -> None:
        now = time.monotonic()
        queue = self._calls[key]
        while queue and queue[0] <= now - self.period_seconds:
            queue.popleft()
        if len(queue) >= self.maximum:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        queue.append(now)
