from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.api import HttpAnalysisPublisher, PublisherError
from app.contracts import MonitoringStatus, StateTransition, TransitionReason, WorkerIngest


def message() -> WorkerIngest:
    return WorkerIngest(
        analysis_id=UUID(int=1),
        session_id=UUID(int=2),
        camera_id=UUID(int=3),
        captured_at=datetime.now(UTC),
        transition_seq=1,
        monitoring_status=MonitoringStatus.STARTING,
        activity=None,
        state=None,
        signals=[],
        quality=None,
        analysis=None,
        transition=StateTransition(
            changed=False,
            previous_state=None,
            reason=TransitionReason.INITIAL,
        ),
    )


class FakeHttpClient:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = statuses
        self.requests = []

    async def post(self, endpoint, **kwargs):
        self.requests.append((endpoint, kwargs))
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return SimpleNamespace(status_code=status)


@pytest.mark.asyncio
async def test_publisher_uses_internal_route_and_bearer_token() -> None:
    client = FakeHttpClient([202])
    publisher = HttpAnalysisPublisher(
        base_url="http://api:8000/",
        internal_token="private-token",
        client=client,
    )
    await publisher.publish(message())
    endpoint, request = client.requests[0]
    assert endpoint == "http://api:8000/api/v1/internal/analyses"
    assert request["headers"] == {"Authorization": "Bearer private-token"}
    assert request["json"]["schema_version"] == "worker-ingest-v1"


@pytest.mark.asyncio
async def test_publisher_retries_transient_status_and_rejects_stale_conflict() -> None:
    retrying = FakeHttpClient([503, 202])
    publisher = HttpAnalysisPublisher(
        base_url="http://api:8000",
        internal_token="token",
        client=retrying,
    )
    await publisher.publish(message())
    assert len(retrying.requests) == 2

    conflict = FakeHttpClient([409])
    publisher = HttpAnalysisPublisher(
        base_url="http://api:8000",
        internal_token="token",
        client=conflict,
    )
    with pytest.raises(PublisherError, match="stale or conflicting"):
        await publisher.publish(message())
    assert len(conflict.requests) == 1


@pytest.mark.asyncio
async def test_publisher_does_not_include_response_body_in_error() -> None:
    client = FakeHttpClient([422])
    publisher = HttpAnalysisPublisher(
        base_url="http://api:8000",
        internal_token="token",
        client=client,
    )
    with pytest.raises(PublisherError, match="HTTP 422"):
        await publisher.publish(message())
