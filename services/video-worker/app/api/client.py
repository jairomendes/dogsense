from __future__ import annotations

import asyncio
from typing import Any, Protocol

from app.contracts.ingest import WorkerIngest


class PublisherError(RuntimeError):
    pass


class AnalysisPublisher(Protocol):
    async def publish(self, payload: WorkerIngest) -> None: ...


class HttpAnalysisPublisher:
    def __init__(
        self,
        *,
        base_url: str,
        internal_token: str,
        timeout_seconds: float = 5.0,
        max_retries: int = 2,
        client: Any | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("DOGSENSE_API_URL must be HTTP(S)")
        if not internal_token:
            raise ValueError("an internal API token is required")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.endpoint = f"{base_url.rstrip('/')}/api/v1/internal/analyses"
        self._token = internal_token
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._client = client

    async def publish(self, payload: WorkerIngest) -> None:
        client = self._client or self._create_client()
        retryable_statuses = {429, 500, 502, 503, 504}
        for attempt in range(self.max_retries + 1):
            try:
                response = await client.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self._token}"},
                    json=payload.model_dump(mode="json"),
                    timeout=self.timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(0.2 * (2**attempt))
                    continue
                raise PublisherError(f"analysis publication failed ({type(exc).__name__})") from exc
            if 200 <= response.status_code < 300:
                return
            if response.status_code == 409:
                # The API returns duplicates as 200; 409 therefore means a stale
                # sequence or another terminal conflict and is not a publication.
                raise PublisherError("analysis publication was rejected as stale or conflicting")
            if response.status_code in retryable_statuses and attempt < self.max_retries:
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            raise PublisherError(f"analysis publication returned HTTP {response.status_code}")

    def _create_client(self) -> Any:
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - checked by container smoke test
            raise PublisherError("httpx is not installed") from exc
        self._client = httpx.AsyncClient()
        return self._client

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()
