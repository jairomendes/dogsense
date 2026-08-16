from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from pydantic import ValidationError

from app.capture.frame import AnalysisWindow
from app.contracts.behavior import BehaviorAnalysis

from .base import InferenceError, InferenceResult, InferenceTimeout, InvalidModelResponse
from .prompt import PROMPT_VERSION, SCHEMA_VERSION, load_prompt


class GeminiAdapter:
    """Google GenAI structured-output adapter with one bounded in-flight request."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 8.0,
        max_retries: int = 1,
        client: Any | None = None,
        clock: Any = time.perf_counter,
    ) -> None:
        if not model:
            raise ValueError("a configured GEMINI_MODEL is required")
        if client is None and not api_key:
            raise ValueError("a Gemini API key is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_retries not in {0, 1}:
            raise ValueError("the MVP permits at most one automatic retry")
        self.model = model
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._client = client
        self._clock = clock
        self._in_flight = asyncio.Lock()

    async def analyze(self, window: AnalysisWindow) -> InferenceResult:
        start = self._clock()
        last_error: InferenceError | None = None
        async with self._in_flight:
            for attempt in range(1, self.max_retries + 2):
                try:
                    async with asyncio.timeout(self.timeout_seconds):
                        response = await self._generate(window)
                    analysis = self._validate_response(response)
                    return InferenceResult(
                        analysis=analysis,
                        model=self.model,
                        latency_ms=max(0, round((self._clock() - start) * 1000)),
                        prompt_version=PROMPT_VERSION,
                        schema_version=SCHEMA_VERSION,
                        attempts=attempt,
                    )
                except TimeoutError:
                    last_error = InferenceTimeout("Gemini request exceeded the configured timeout")
                except InvalidModelResponse as exc:
                    last_error = exc
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Do not retain or expose the provider response or frame content.
                    last_error = InferenceError(f"Gemini request failed ({type(exc).__name__})")
        assert last_error is not None
        raise last_error

    async def _generate(self, window: AnalysisWindow) -> Any:
        client = self._client or self._create_client()
        contents: list[Any] = [load_prompt()]
        contents.extend(
            {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": frame.jpeg,
                }
            }
            for frame in window.frames
        )
        return await client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config={
                "temperature": 0,
                "response_mime_type": "application/json",
                "response_json_schema": BehaviorAnalysis.model_json_schema(),
            },
        )

    def _create_client(self) -> Any:
        try:
            from google import genai  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - checked by container smoke test
            raise InferenceError("google-genai is not installed") from exc
        self._client = genai.Client(api_key=self._api_key)
        return self._client

    @staticmethod
    def _validate_response(response: Any) -> BehaviorAnalysis:
        text = getattr(response, "text", None)
        if not isinstance(text, str):
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, BehaviorAnalysis):
                return parsed
            if isinstance(parsed, dict):
                try:
                    return BehaviorAnalysis.model_validate(parsed)
                except ValidationError as exc:
                    raise InvalidModelResponse(
                        "Gemini returned an invalid structured payload"
                    ) from exc
            raise InvalidModelResponse("Gemini response did not contain JSON text")
        stripped = text.strip()
        if not stripped.startswith("{") or not stripped.endswith("}"):
            raise InvalidModelResponse("Gemini returned unexpected text around the JSON object")
        try:
            payload = json.loads(stripped)
            return BehaviorAnalysis.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise InvalidModelResponse("Gemini returned an invalid structured payload") from exc
