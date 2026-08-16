# DogSense contracts

Versioned, provider-neutral contracts shared by the worker, API and web app.

- `json-schema/behavior-analysis-v1.schema.json` validates raw Google AI output.
- `json-schema/worker-ingest-v1.schema.json` validates the worker-to-API message.
- `json-schema/enums-v1.json` is the canonical list of domain values.
- `generated-types/index.ts` contains the matching TypeScript declarations.

All schemas reject unknown properties. A technical monitoring status is deliberately
separate from a probable behavioral state. For a technical failure the ingest
contract keeps a stable shape by sending `null` for inference-derived fields instead
of fabricating a behavioral result.
