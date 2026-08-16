-- Transactional snapshot used by the single-process MVP API.
-- The API applies the same idempotent DDL at startup; keeping it here makes
-- `make migrate` useful and fresh PostgreSQL volumes explicit.
CREATE TABLE IF NOT EXISTS dogsense_api_snapshot (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

