"""
Seed a dev tenant + project + API key directly in Postgres.

Phase 2: prefer registering through the auth-service (POST /auth/register)
once it's running. This script is kept as a fast-path for local dev when
the auth-service isn't started yet.

Usage:
    pip install asyncpg bcrypt
    python seed_dev.py

Prints the raw API key — store it, it won't be shown again.
"""
import asyncio
import hashlib
import hmac
import os
import secrets

import asyncpg
import bcrypt

DSN = os.environ.get(
    "POSTGRES_DSN",
    "postgresql://opentelm:opentelm@localhost:5432/opentelm",
)
PEPPER = os.environ.get("API_KEY_PEPPER", "opentelm-key-secret").encode()


def make_key() -> tuple[str, str, str]:
    raw = "opentelm_live_" + secrets.token_hex(16)
    key_hash = hmac.new(PEPPER, raw.encode(), hashlib.sha256).hexdigest()
    return raw, key_hash, raw[:8]


async def seed():
    conn = await asyncpg.connect(DSN)

    pw_hash = bcrypt.hashpw(b"devpassword", bcrypt.gensalt(rounds=12)).decode()
    tenant_id = await conn.fetchval(
        """
        INSERT INTO tenants (name, email, password_hash) VALUES ($1, $2, $3)
        RETURNING id::text
        """,
        "Dev Tenant", "dev@example.com", pw_hash,
    )
    project_id = await conn.fetchval(
        "INSERT INTO projects (tenant_id, name) VALUES ($1, $2) RETURNING id::text",
        tenant_id, "my-first-project",
    )
    raw_key, key_hash, prefix = make_key()
    await conn.execute(
        """
        INSERT INTO api_keys (tenant_id, project_id, key_hash, key_prefix, permissions)
        VALUES ($1, $2, $3, $4, $5)
        """,
        tenant_id, project_id, key_hash, prefix, "write",
    )
    await conn.close()
    print(f"Tenant ID  : {tenant_id}")
    print(f"Project ID : {project_id}")
    print(f"Login      : dev@example.com / devpassword")
    print(f"API Key    : {raw_key}")
    print("\nPass the API Key to smoke_test.py --api-key <key>")


if __name__ == "__main__":
    asyncio.run(seed())
