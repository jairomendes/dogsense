from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.contracts import MonitoringStatus
from app.health import create_health_app
from app.worker import WorkerRuntime


@pytest.mark.asyncio
async def test_ready_exposes_bounded_pipeline_counters() -> None:
    runtime = WorkerRuntime(
        running=True,
        ready=True,
        status=MonitoringStatus.ANALYZING,
        last_frame_at=datetime(2026, 8, 15, 19, 0, tzinfo=UTC),
        frames_received=42,
        windows_scheduled=3,
        windows_insufficient=1,
        inference_requests=2,
        publications=2,
    )
    transport = httpx.ASGITransport(app=create_health_app(runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "monitoring_status": "analyzing",
        "last_frame_at": "2026-08-15T19:00:00+00:00",
        "frames_received": 42,
        "frames_dropped": 0,
        "windows_scheduled": 3,
        "windows_insufficient": 1,
        "windows_dropped": 0,
        "inference_requests": 2,
        "inference_errors": 0,
        "invalidated_results": 0,
        "publications": 2,
        "publication_errors": 0,
        "last_published_transition_seq": None,
        "inference_in_flight": False,
        "queue_depth": 0,
    }


@pytest.mark.asyncio
async def test_not_ready_returns_503_with_counters() -> None:
    transport = httpx.ASGITransport(app=create_health_app(WorkerRuntime()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["frames_received"] == 0
