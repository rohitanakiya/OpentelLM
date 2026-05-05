-- OpenTelLM Postgres schema
-- Phase 1: tenants, projects, api_keys
-- Phase 2: adds password_hash, permissions, is_active for the auth service.
-- Idempotent — safe to re-run on an existing Phase 1 install.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- tenants
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT,                                    -- bcrypt; nullable for legacy Phase 1 seed rows
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS password_hash TEXT;

-- ---------------------------------------------------------------------------
-- projects
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE INDEX IF NOT EXISTS projects_tenant_idx ON projects(tenant_id);

-- ---------------------------------------------------------------------------
-- api_keys
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_keys (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    project_id   UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    -- HMAC-SHA256(pepper, raw_key); the raw key is never stored
    key_hash     TEXT UNIQUE NOT NULL,
    -- First 8 chars of the raw key, shown in UI lists. e.g. "opentelm"
    key_prefix   TEXT NOT NULL,
    permissions  TEXT NOT NULL DEFAULT 'write'
                   CHECK (permissions IN ('read', 'write', 'admin')),
    is_active    BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ
);

-- Phase 1 → Phase 2 migration: add new columns and backfill from old `scope`
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS permissions TEXT;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS is_active   BOOLEAN NOT NULL DEFAULT true;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'api_keys' AND column_name = 'scope'
    ) THEN
        UPDATE api_keys SET permissions = COALESCE(permissions, scope) WHERE permissions IS NULL;
    END IF;

    -- Enforce permissions NOT NULL only after backfill
    BEGIN
        ALTER TABLE api_keys ALTER COLUMN permissions SET NOT NULL;
    EXCEPTION WHEN others THEN
        -- already NOT NULL
        NULL;
    END;

    -- Add the CHECK constraint if missing
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'api_keys_permissions_check'
    ) THEN
        ALTER TABLE api_keys
          ADD CONSTRAINT api_keys_permissions_check
          CHECK (permissions IN ('read', 'write', 'admin'));
    END IF;
END $$;

-- Hot lookup path: ingest-api validates by key_hash on every request.
-- key_hash already has a UNIQUE constraint (= unique btree index), so we
-- only need supplementary indexes on tenant_id for the auth-service list views.
CREATE INDEX IF NOT EXISTS api_keys_tenant_idx ON api_keys(tenant_id);
