"""
/projects/* and /projects/{id}/keys/* routes.

EVERY query in this module includes `WHERE tenant_id = $1` as a hard
isolation boundary. This is enforced at the SQL layer so that even an
application-layer bug cannot leak across tenants.
"""
from __future__ import annotations

import asyncpg
import json
from fastapi import APIRouter, Depends, HTTPException, status

from config import settings
from core.database import get_pool
from core.redis import get_redis
from core.security import generate_api_key
from dependencies import CurrentTenant, get_current_tenant
from models.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyPublic,
    ProjectCreate,
    ProjectPublic,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _apikey_cache_key(key_hash: str) -> str:
    return f"apikey:{key_hash}"


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
@router.post("", response_model=ProjectPublic, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    current: CurrentTenant = Depends(get_current_tenant),
):
    pool = get_pool()
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO projects (tenant_id, name)
            VALUES ($1, $2)
            RETURNING id::text, tenant_id::text, name, created_at
            """,
            current.tenant_id, body.name,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "project_name_already_exists"},
        )
    return ProjectPublic(**dict(row))


@router.get("", response_model=list[ProjectPublic])
async def list_projects(current: CurrentTenant = Depends(get_current_tenant)):
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT id::text, tenant_id::text, name, created_at
        FROM projects
        WHERE tenant_id = $1
        ORDER BY created_at DESC
        """,
        current.tenant_id,
    )
    return [ProjectPublic(**dict(r)) for r in rows]


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    current: CurrentTenant = Depends(get_current_tenant),
):
    pool = get_pool()
    redis = get_redis()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Find every key hash that belongs to this project under this tenant
            # so we can purge them from the Redis cache after delete.
            key_rows = await conn.fetch(
                """
                SELECT key_hash FROM api_keys
                WHERE project_id = $1 AND tenant_id = $2
                """,
                project_id, current.tenant_id,
            )

            # The cascade FK on projects → api_keys will delete the keys when
            # the project row goes; we still issue the explicit delete for clarity.
            result = await conn.execute(
                """
                DELETE FROM projects
                WHERE id = $1 AND tenant_id = $2
                """,
                project_id, current.tenant_id,
            )

    # asyncpg's execute() returns "DELETE <n>"
    if result.endswith(" 0"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "project_not_found"},
        )

    # Best-effort cache purge — if Redis is down, the cache TTL will eventually
    # expire entries and the Postgres lookup will fail (key row already gone).
    if key_rows:
        await redis.delete(*[_apikey_cache_key(r["key_hash"]) for r in key_rows])

    return None


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------
@router.post("/{project_id}/keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    project_id: str,
    body: ApiKeyCreate,
    current: CurrentTenant = Depends(get_current_tenant),
):
    pool = get_pool()
    redis = get_redis()

    # Verify the project belongs to this tenant. Inserting an api_key without
    # this check would leak: a malicious caller could supply someone else's
    # project_id (a UUID is guessable in principle).
    project_row = await pool.fetchrow(
        """
        SELECT 1 FROM projects WHERE id = $1 AND tenant_id = $2
        """,
        project_id, current.tenant_id,
    )
    if project_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "project_not_found"},
        )

    raw_key, key_hash, prefix = generate_api_key()

    row = await pool.fetchrow(
        """
        INSERT INTO api_keys (tenant_id, project_id, key_hash, key_prefix, permissions)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id::text, project_id::text, key_prefix, permissions, created_at
        """,
        current.tenant_id, project_id, key_hash, prefix, body.permissions,
    )

    # Pre-warm the Redis cache so the very first ingest call is a cache hit
    cache_value = json.dumps({
        "tenant_id": current.tenant_id,
        "project_id": project_id,
        "permissions": body.permissions,
    }).encode()
    await redis.set(_apikey_cache_key(key_hash), cache_value, ex=settings.apikey_cache_ttl_s)

    return ApiKeyCreated(
        id=row["id"],
        project_id=row["project_id"],
        api_key=raw_key,                # ← shown ONCE
        key_prefix=row["key_prefix"],
        permissions=row["permissions"],
        created_at=row["created_at"],
    )


@router.get("/{project_id}/keys", response_model=list[ApiKeyPublic])
async def list_api_keys(
    project_id: str,
    current: CurrentTenant = Depends(get_current_tenant),
):
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT id::text, project_id::text, key_prefix, permissions,
               is_active, created_at, last_used_at
        FROM api_keys
        WHERE project_id = $1 AND tenant_id = $2
        ORDER BY created_at DESC
        """,
        project_id, current.tenant_id,
    )
    return [ApiKeyPublic(**dict(r)) for r in rows]


@router.delete("/{project_id}/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    project_id: str,
    key_id: str,
    current: CurrentTenant = Depends(get_current_tenant),
):
    pool = get_pool()
    redis = get_redis()

    row = await pool.fetchrow(
        """
        UPDATE api_keys
        SET is_active = false, revoked_at = now()
        WHERE id = $1 AND project_id = $2 AND tenant_id = $3
        RETURNING key_hash
        """,
        key_id, project_id, current.tenant_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "api_key_not_found"},
        )

    # Purge the cache entry so ingest-api stops accepting this key immediately,
    # rather than waiting up to 24h for the entry to expire.
    await redis.delete(_apikey_cache_key(row["key_hash"]))
    return None
