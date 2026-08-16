from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest


def analysis_payload(bootstrap: dict, *, analysis_id: str | None = None, sequence: int = 0) -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": "worker-ingest-v1",
        "analysis_id": analysis_id or str(uuid4()),
        "session_id": bootstrap["session_id"],
        "camera_id": bootstrap["camera_id"],
        "captured_at": now,
        "transition_seq": sequence,
        "monitoring_status": "analyzing",
        "activity": {"label": "pacing", "confidence": 0.88},
        "state": {
            "label": "stress_signals",
            "confidence": 0.81,
            "duration_seconds": 12,
            "started_at": now,
        },
        "signals": [{"name": "repetitive_movement", "confidence": 0.92}],
        "quality": {
            "dog_visible": True,
            "dogs_detected": 1,
            "observation_quality": 0.88,
            "body_visibility": 0.91,
            "face_visibility": 0.46,
        },
        "analysis": {
            "schema_version": "behavior-analysis-v1",
            "prompt_version": "behavior-observer-v1",
            "model": "configured-gemini-model",
            "latency_ms": 1830,
        },
        "transition": {"changed": True, "previous_state": "relaxed", "reason": "stable transition"},
    }


def test_internal_ingest_requires_token_and_is_idempotent(client):
    bootstrap = client.get("/api/v1/demo/bootstrap").json()
    payload = analysis_payload(bootstrap)
    assert client.post("/api/v1/internal/analyses", json=payload).status_code == 401

    headers = {"X-Internal-Token": "test-worker-token"}
    accepted = client.post("/api/v1/internal/analyses", json=payload, headers=headers)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["accepted"] is True

    duplicate = client.post("/api/v1/internal/analyses", json=payload, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["accepted"] is False
    assert duplicate.json()["duplicate"] is True

    stale = analysis_payload(bootstrap, sequence=0)
    assert client.post("/api/v1/internal/analyses", json=stale, headers=headers).status_code == 409

    current = client.get(f"/api/v1/dogs/{bootstrap['dog_id']}/state/current").json()
    assert current["state"]["label"] == "stress_signals"
    assert current["sequence"] == 0


def test_technical_status_cannot_fabricate_inference(client):
    bootstrap = client.get("/api/v1/demo/bootstrap").json()
    payload = analysis_payload(bootstrap)
    payload.update(
        monitoring_status="camera_offline",
        activity=None,
        state=None,
        quality=None,
        analysis=None,
        signals=[],
    )
    headers = {"X-Internal-Token": "test-worker-token"}
    response = client.post("/api/v1/internal/analyses", json=payload, headers=headers)
    assert response.status_code == 200, response.text

    payload["analysis_id"] = str(uuid4())
    payload["activity"] = {"label": "resting", "confidence": 0.9}
    invalid = client.post("/api/v1/internal/analyses", json=payload, headers=headers)
    assert invalid.status_code == 422


@pytest.mark.parametrize(
    ("monitoring_status", "dog_visible", "dogs_detected", "body_visibility"),
    [
        ("dog_not_visible", False, 0, 0.0),
        ("multiple_dogs_detected", True, 2, 0.9),
        ("insufficient_visibility", True, 1, 0.2),
    ],
)
def test_observation_status_accepts_quality_and_analysis(
    client,
    monitoring_status,
    dog_visible,
    dogs_detected,
    body_visibility,
):
    bootstrap = client.get("/api/v1/demo/bootstrap").json()
    payload = analysis_payload(bootstrap)
    payload.update(
        monitoring_status=monitoring_status,
        activity=None,
        state=None,
        signals=[],
    )
    payload["quality"].update(
        dog_visible=dog_visible,
        dogs_detected=dogs_detected,
        body_visibility=body_visibility,
    )
    response = client.post(
        "/api/v1/internal/analyses",
        json=payload,
        headers={"X-Internal-Token": "test-worker-token"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["current_state"]["monitoring_status"] == monitoring_status
    assert response.json()["current_state"]["quality"] is not None
    assert response.json()["current_state"]["analysis"] is not None


def test_observation_status_requires_metadata_and_forbids_behavior(client):
    bootstrap = client.get("/api/v1/demo/bootstrap").json()
    headers = {"X-Internal-Token": "test-worker-token"}
    payload = analysis_payload(bootstrap)
    payload.update(
        monitoring_status="dog_not_visible",
        activity=None,
        state=None,
        signals=[],
        quality=None,
    )
    missing_quality = client.post("/api/v1/internal/analyses", json=payload, headers=headers)
    assert missing_quality.status_code == 422

    payload = analysis_payload(bootstrap)
    payload.update(
        monitoring_status="insufficient_visibility",
        state=None,
        signals=[],
    )
    fabricated_activity = client.post("/api/v1/internal/analyses", json=payload, headers=headers)
    assert fabricated_activity.status_code == 422
