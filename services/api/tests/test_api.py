from __future__ import annotations


def test_health_bootstrap_and_demo_aliases(client):
    live = client.get("/health/live")
    assert live.status_code == 200
    assert live.json()["status"] == "ok"

    bootstrap = client.get("/api/v1/demo/bootstrap")
    assert bootstrap.status_code == 200
    assert bootstrap.json()["camera_id"] == "00000000-0000-0000-0000-000000000001"
    assert client.get("/api/v1/dogs/demo-dog").json()["name"] == "Luna"
    assert client.get("/api/v1/cameras/demo-camera").status_code == 200
    assert client.get("/api/v1/monitoring/sessions/demo-session").status_code == 200


def test_camera_credentials_are_encrypted_and_redacted(client):
    dog_id = client.get("/api/v1/demo/bootstrap").json()["dog_id"]
    secret = "super-secret-password"
    response = client.post(
        "/api/v1/cameras",
        json={
            "dog_id": dog_id,
            "name": "Living room",
            "rtsp_url": f"rtsp://alice:{secret}@192.0.2.10:8554/private/path?token=hidden",
            "username": "alice",
            "password": secret,
            "active": True,
        },
    )
    assert response.status_code == 201, response.text
    serialized = response.text
    assert secret not in serialized
    assert "alice" not in serialized
    assert "private/path" not in serialized
    assert "encrypted_credentials" not in serialized
    camera = response.json()
    assert camera["rtsp_url_redacted"] == "rtsp://192.0.2.10:8554/***"

    tested = client.post(f"/api/v1/cameras/{camera['id']}/test")
    assert tested.status_code == 200
    assert tested.json()["frames_received"] == 5


def test_speech_is_cached_and_audio_is_playable(client):
    event_id = client.get("/api/v1/demo/bootstrap").json()["current_event_id"]
    first = client.post(f"/api/v1/events/{event_id}/speech", json={"language": "en"})
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "ready"
    assert "diagnos" not in first.json()["text"].lower()

    second = client.post(f"/api/v1/events/{event_id}/speech", json={"language": "en"})
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]

    audio = client.get(f"/api/v1/speech/{first.json()['id']}/audio")
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")
    assert audio.content.startswith(b"RIFF")


def test_receipt_is_idempotent_and_verifiable(client):
    event_id = client.get("/api/v1/demo/bootstrap").json()["current_event_id"]
    first = client.post(f"/api/v1/events/{event_id}/receipt")
    assert first.status_code == 201, first.text
    receipt = first.json()
    assert receipt["status"] == "confirmed"
    assert "Luna" not in first.text
    assert len(receipt["event_hash"]) == 64

    second = client.post(f"/api/v1/events/{event_id}/receipt")
    assert second.status_code == 200
    assert second.json()["id"] == receipt["id"]
    assert second.json()["transaction_signature"] == receipt["transaction_signature"]

    verified = client.post(f"/api/v1/receipts/{receipt['id']}/verify")
    assert verified.status_code == 200
    assert verified.json()["verified"] is True
    assert verified.json()["snapshot_matches_event"] is True


def test_feedback_and_analytics(client):
    bootstrap = client.get("/api/v1/demo/bootstrap").json()
    event_id = bootstrap["current_event_id"]
    feedback = client.post(
        f"/api/v1/events/{event_id}/feedback",
        json={"correct": False, "corrected_state": "alert", "comment": "Looked toward the door"},
    )
    assert feedback.status_code == 201
    assert feedback.json()["corrected_state"] == "alert"

    assert client.get(f"/api/v1/dogs/{bootstrap['dog_id']}/analytics/summary").json()["event_count"] >= 1
    distribution = client.get(f"/api/v1/dogs/{bootstrap['dog_id']}/analytics/distribution").json()
    assert any(item["state"] == "relaxed" for item in distribution["states"])


def test_websocket_sends_recovery_snapshot(client):
    with client.websocket_connect("/api/v1/live/dogs/demo-dog?token=ignored-in-demo") as websocket:
        message = websocket.receive_json()
        assert message["type"] == "snapshot"
        assert message["current_state"]["state"]["label"] == "relaxed"
        assert message["recent_events"]
