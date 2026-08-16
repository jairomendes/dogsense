from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from conftest import FIXTURES, load_analysis

from app.inference import (
    DeterministicFakeAdapter,
    FakeScenarioStep,
    GeminiAdapter,
    InferenceTimeout,
    InvalidModelResponse,
    load_prompt,
)


class ScriptedModels:
    def __init__(self, responses, *, delay: float = 0.0) -> None:
        self.responses = list(responses)
        self.delay = delay
        self.calls = []
        self.active = 0
        self.maximum_active = 0

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
            return response
        finally:
            self.active -= 1


def client_for(models: ScriptedModels):
    return SimpleNamespace(aio=SimpleNamespace(models=models))


def test_versioned_prompt_contains_guardrails() -> None:
    prompt = load_prompt()
    assert "behavior-observer-v1" in prompt
    assert "behavior-analysis-v1" in prompt
    assert "Never diagnose" in prompt
    assert "chronological sequence" in prompt
    assert "Return exactly one JSON object" in prompt


@pytest.mark.asyncio
async def test_fake_is_deterministic_and_returns_deep_copies(window_factory) -> None:
    clock_value = [100.0]
    adapter = DeterministicFakeAdapter(clock=lambda: clock_value[0])
    first = await adapter.analyze(window_factory())
    second = await adapter.analyze(window_factory(2))
    assert first.analysis == second.analysis
    assert first.analysis is not second.analysis
    assert adapter.calls == 2


@pytest.mark.asyncio
async def test_fake_scenario_follows_elapsed_clock(window_factory) -> None:
    clock_value = [5.0]
    adapter = DeterministicFakeAdapter(
        [
            FakeScenarioStep(0, analysis=load_analysis("relaxed.json")),
            FakeScenarioStep(10, analysis=load_analysis("alert.json")),
        ],
        clock=lambda: clock_value[0],
    )
    assert (await adapter.analyze(window_factory())).analysis.state.label.value == "relaxed"
    clock_value[0] = 15.1
    assert (await adapter.analyze(window_factory(2))).analysis.state.label.value == "alert"


def test_fake_fixture_file_uses_strict_contract() -> None:
    adapter = DeterministicFakeAdapter.from_files(fixture_path=FIXTURES / "relaxed.json")
    assert adapter.steps[0].analysis is not None
    with pytest.raises(ValueError, match="does not match"):
        DeterministicFakeAdapter.from_files(fixture_path=FIXTURES / "invalid-extra-field.json")


@pytest.mark.asyncio
async def test_fake_can_inject_timeout(window_factory) -> None:
    adapter = DeterministicFakeAdapter([FakeScenarioStep(0, error="timeout")])
    with pytest.raises(InferenceTimeout):
        await adapter.analyze(window_factory())


@pytest.mark.asyncio
async def test_gemini_sends_all_frames_with_structured_schema(window_factory) -> None:
    raw = json.loads((FIXTURES / "relaxed.json").read_text())
    models = ScriptedModels([SimpleNamespace(text=json.dumps(raw))])
    adapter = GeminiAdapter(model="configured-model", client=client_for(models), max_retries=0)
    window = window_factory(frame_count=8)
    result = await adapter.analyze(window)
    assert result.model == "configured-model"
    assert result.attempts == 1
    request = models.calls[0]
    assert len(request["contents"]) == 9
    assert request["config"]["response_mime_type"] == "application/json"
    assert request["config"]["response_json_schema"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_gemini_retries_once_after_invalid_response(window_factory) -> None:
    raw = json.loads((FIXTURES / "relaxed.json").read_text())
    models = ScriptedModels(
        [SimpleNamespace(text="not-json"), SimpleNamespace(text=json.dumps(raw))]
    )
    adapter = GeminiAdapter(model="configured-model", client=client_for(models), max_retries=1)
    result = await adapter.analyze(window_factory())
    assert result.attempts == 2
    assert len(models.calls) == 2


@pytest.mark.asyncio
async def test_gemini_rejects_markdown_even_when_json_inside(window_factory) -> None:
    raw = (FIXTURES / "relaxed.json").read_text()
    models = ScriptedModels([SimpleNamespace(text=f"```json\n{raw}\n```")])
    adapter = GeminiAdapter(model="configured-model", client=client_for(models), max_retries=0)
    with pytest.raises(InvalidModelResponse):
        await adapter.analyze(window_factory())


@pytest.mark.asyncio
async def test_gemini_timeout_is_bounded(window_factory) -> None:
    models = ScriptedModels([SimpleNamespace(text="{}")], delay=0.05)
    adapter = GeminiAdapter(
        model="configured-model",
        client=client_for(models),
        timeout_seconds=0.001,
        max_retries=0,
    )
    with pytest.raises(InferenceTimeout):
        await adapter.analyze(window_factory())


@pytest.mark.asyncio
async def test_gemini_serializes_concurrent_calls(window_factory) -> None:
    raw = json.loads((FIXTURES / "relaxed.json").read_text())
    response = SimpleNamespace(text=json.dumps(raw))
    models = ScriptedModels([response, response], delay=0.01)
    adapter = GeminiAdapter(model="configured-model", client=client_for(models), max_retries=0)
    await asyncio.gather(adapter.analyze(window_factory(1)), adapter.analyze(window_factory(2)))
    assert models.maximum_active == 1
