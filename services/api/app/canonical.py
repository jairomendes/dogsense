from __future__ import annotations

import hashlib
import hmac
import json
import math
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from app.models import StateEvent

CANONICAL_VERSION = "dogsense-event-v1"
_PRECISION = Decimal("0.000001")


def _timestamp(value: datetime) -> str:
    utc = value.astimezone(UTC)
    return utc.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _number(value: float | Decimal) -> int | float:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("canonical JSON does not permit non-finite numbers")
    normalized = Decimal(str(value)).quantize(_PRECISION, rounding=ROUND_HALF_EVEN).normalize()
    if normalized == normalized.to_integral():
        return int(normalized)
    return float(format(normalized, "f"))


def pseudonymize(identifier: str, key: str, *, length: int = 10) -> str:
    return hmac.new(key.encode("utf-8"), identifier.encode("utf-8"), hashlib.sha256).hexdigest()[:length]


def event_snapshot(event: StateEvent, analytics_hmac_key: str) -> dict[str, Any]:
    if event.ended_at is None:
        raise ValueError("only ended events can be canonicalized")
    return {
        "canonical_version": CANONICAL_VERSION,
        "event_id": event.id,
        "dog_id_hash": pseudonymize(event.dog_id, analytics_hmac_key),
        "started_at": _timestamp(event.started_at),
        "ended_at": _timestamp(event.ended_at),
        "state": getattr(event.state, "value", event.state),
        "activity": getattr(event.activity, "value", event.activity),
        "confidence_avg": _number(event.confidence_avg),
        "signals": sorted({signal.name for signal in event.signals}),
        "prompt_version": event.prompt_version,
    }


def canonical_json(value: dict[str, Any]) -> bytes:
    cleaned = {key: item for key, item in value.items() if item is not None}
    return json.dumps(
        cleaned,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(snapshot)).hexdigest()


def receipt_memo(event_id: str, event_hash: str) -> str:
    return f"DOGSENSE:v1:{event_id}:{event_hash}"


def hash_from_memo(memo: str | None) -> str | None:
    if not memo:
        return None
    pieces = memo.split(":")
    if len(pieces) != 4 or pieces[:2] != ["DOGSENSE", "v1"]:
        return None
    candidate = pieces[3]
    if len(candidate) != 64 or any(character not in "0123456789abcdef" for character in candidate):
        return None
    return candidate
