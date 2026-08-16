from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import jsonschema
import pytest
from conftest import FIXTURES
from pydantic import ValidationError

from app.contracts import (
    ActivityLabel,
    BehaviorAnalysis,
    MonitoringStatus,
    SignalName,
    StateLabel,
    StateTransition,
    TransitionReason,
    WorkerIngest,
    sanitize_summary,
)

ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / "packages" / "contracts"


def payload(name: str = "relaxed.json") -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name",
    [
        "relaxed.json",
        "engaged.json",
        "alert.json",
        "stress-signals.json",
        "dog-not-visible.json",
        "multiple-dogs.json",
        "insufficient-visibility.json",
    ],
)
def test_valid_fixtures_match_pydantic_and_json_schema(name: str) -> None:
    raw = payload(name)
    assert BehaviorAnalysis.model_validate(raw).schema_version == "behavior-analysis-v1"
    schema = json.loads(
        (CONTRACTS / "json-schema" / "behavior-analysis-v1.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(raw)


@pytest.mark.parametrize("score", [0.0, 1.0])
def test_score_boundaries_are_valid(score: float) -> None:
    raw = payload()
    raw["observation_quality"] = score
    assert BehaviorAnalysis.model_validate(raw).observation_quality == score


@pytest.mark.parametrize("score", [-0.0001, 1.0001])
def test_out_of_range_score_is_rejected(score: float) -> None:
    raw = payload()
    raw["state_scores"]["relaxed"] = score
    with pytest.raises(ValidationError):
        BehaviorAnalysis.model_validate(raw)


def test_unknown_fields_and_enums_are_rejected() -> None:
    with pytest.raises(ValidationError):
        BehaviorAnalysis.model_validate(payload("invalid-extra-field.json"))
    raw = payload()
    raw["activity"]["label"] = "jumping"
    with pytest.raises(ValidationError):
        BehaviorAnalysis.model_validate(raw)


@pytest.mark.parametrize(
    ("path", "bad_value"),
    [
        (("dogs_detected",), True),
        (("dog_visible",), 1),
        (("activity", "confidence"), "0.8"),
    ],
)
def test_scalar_types_are_strict(path: tuple[str, ...], bad_value: object) -> None:
    raw = payload()
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad_value
    with pytest.raises(ValidationError):
        BehaviorAnalysis.model_validate(raw)


def test_visibility_relationship_is_enforced() -> None:
    raw = payload("dog-not-visible.json")
    raw["dog_visible"] = True
    with pytest.raises(ValidationError, match="dog_visible"):
        BehaviorAnalysis.model_validate(raw)


def test_signal_limit_uniqueness_and_summary_limit() -> None:
    raw = payload()
    raw["signals"] = [{"name": name.value, "confidence": 0.8} for name in list(SignalName)[:6]]
    with pytest.raises(ValidationError):
        BehaviorAnalysis.model_validate(raw)
    duplicate = payload()
    duplicate["signals"] = [duplicate["signals"][0], duplicate["signals"][0]]
    with pytest.raises(ValidationError, match="unique"):
        BehaviorAnalysis.model_validate(duplicate)
    summary = payload()
    summary["summary"] = "x" * 301
    with pytest.raises(ValidationError):
        BehaviorAnalysis.model_validate(summary)


def test_summary_filter_escapes_markup_and_removes_controls() -> None:
    assert sanitize_summary(" <script>\x00alert('x')</script> ") == (
        "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;"
    )


def test_enum_catalog_does_not_drift_from_python() -> None:
    catalog = json.loads((CONTRACTS / "json-schema" / "enums-v1.json").read_text())
    assert catalog["activity"] == [item.value for item in ActivityLabel]
    assert catalog["state"] == [item.value for item in StateLabel]
    assert catalog["monitoring_status"] == [item.value for item in MonitoringStatus]
    assert catalog["signal"] == [item.value for item in SignalName]


def test_worker_ingest_allows_null_inference_fields_for_technical_status() -> None:
    message = WorkerIngest(
        analysis_id=UUID("00000000-0000-0000-0000-000000000001"),
        session_id=UUID("00000000-0000-0000-0000-000000000002"),
        camera_id=UUID("00000000-0000-0000-0000-000000000003"),
        captured_at=datetime.now(UTC),
        transition_seq=1,
        monitoring_status=MonitoringStatus.CAMERA_OFFLINE,
        activity=None,
        state=None,
        signals=[],
        quality=None,
        analysis=None,
        transition=StateTransition(
            changed=False,
            previous_state=None,
            reason=TransitionReason.CAMERA_OFFLINE,
        ),
    )
    schema = json.loads((CONTRACTS / "json-schema" / "worker-ingest-v1.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(message.model_dump(mode="json"))


def test_naive_ingest_timestamp_is_rejected() -> None:
    raw = {
        "analysis_id": UUID(int=1),
        "session_id": UUID(int=2),
        "camera_id": UUID(int=3),
        "captured_at": datetime.now(),
        "transition_seq": 1,
        "monitoring_status": "starting",
        "activity": None,
        "state": None,
        "signals": [],
        "quality": None,
        "analysis": None,
        "transition": {"changed": False, "previous_state": None, "reason": "initial"},
    }
    with pytest.raises(ValidationError, match="timezone"):
        WorkerIngest.model_validate(raw)
