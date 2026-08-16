# DogSense video worker

The worker decodes the MediaMTX RTSP stream, holds a four-second memory-only ring
buffer, samples six to eight representative JPEG frames and publishes only validated,
stabilized metadata to the internal API.

Defaults use the deterministic fake. Set `DOGSENSE_AI_PROVIDER=gemini`,
`GEMINI_API_KEY`, and `GEMINI_MODEL` to enable the real Google GenAI adapter. The
model name is always configuration; it is never fixed in code.

For controlled demos, `DOGSENSE_FAKE_FIXTURE_PATH` accepts one
`behavior-analysis-v1` object. `DOGSENSE_FAKE_SCENARIO_PATH` accepts:

```json
{
  "loop": false,
  "steps": [
    { "at_seconds": 0, "analysis": { "schema_version": "behavior-analysis-v1" } }
  ]
}
```

Each full `analysis` must pass the same strict Pydantic contract as a provider response.
Steps may instead use `"error": "timeout"` or `"error": "unavailable"` for fault
injection.

The process exposes `/health/live` and `/health/ready` on port 8081. It sends
`worker-ingest-v1` messages to `POST /api/v1/internal/analyses` with a bearer token.
Frames and provider payload text are never logged or written to disk.

Run tests from this directory after installing `requirements-dev.txt`:

```bash
pytest
```
