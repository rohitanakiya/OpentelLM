"""
API key validation for the ingest endpoint.

This is the hard tenant-isolation boundary. A request that gets past this
function is guaranteed to have a resolved tenant_id and project_id, and
those values flow into every downstream Kafka message and ClickHouse row.

The Redis cache format must match what auth-service writes:
    key  : apikey:{key_hash}
    value: JSON {"tenant_id": ..., "project_id": ..., "permissions": ...}
    ttl  : 24h
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Optional

import asyncpg
import redis.asyncio as aioredis
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings

bearer = HTTPBearer()


@dataclass(frozen=True)
class ApiKeyIdentity:
    tenant_id: str
    project_id: str
    permissions: str   # 'read' | 'write' | 'admin'


def _cache_key(key_hash: str) -> str:
    return f"apikey:{key_hash}"


def _hash_key(raw_key: str) -> str:
    return hmac.new(
        settings.api_key_pepper.encode("utf-8"),
        raw_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def validate_api_key(
    credentials: HTTPAuthorizationCredentials = Security(bearer),
    pg: asyncpg.Pool = None,
    redis: aioredis.Redis = None,
) -> ApiKeyIdentity:
    """
    Resolve an Authorization: Bearer <api_key> to (tenant_id, project_id, permissions).
    Raises 401 if the key is unknown, revoked, or malformed.
    """
    raw_key = credentials.credentials
    key_hash = _hash_key(raw_key)

    # 1. Fast path — Redis cache
    cached = await redis.get(_cache_key(key_hash))
    identity = _parse_cached(cached) if cached else None
    if identity is not None:
        return identity

    # 2. Slow path — Postgres
    row = await pg.fetchrow(
        """
        SELECT tenant_id::text, project_id::text, permissions
        FROM api_keys
        WHERE key_hash = $1
          AND is_active = true
          AND revoked_at IS NULL
        """,
        key_hash,
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_api_key"},
        )

    identity = ApiKeyIdentity(
        tenant_id=row["tenant_id"],
        project_id=row["project_id"],
        permissions=row["permissions"],
    )

    # Fire-and-forget: bump last_used_at, repopulate Redis
    await pg.execute(
        "UPDATE api_keys SET last_used_at = now() WHERE key_hash = $1",
        key_hash,
    )
    await redis.set(
        _cache_key(key_hash),
        json.dumps({
            "tenant_id": identity.tenant_id,
            "project_id": identity.project_id,
            "permissions": identity.permissions,
        }).encode(),
        ex=settings.apikey_cache_ttl_s,
    )

    return identity


def _parse_cached(raw: bytes | str) -> Optional[ApiKeyIdentity]:
    """Decode the auth-service JSON format. Return None if it's malformed
    (e.g. legacy 'tenant:project' colon-delimited values from Phase 1)."""
    try:
        text = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        data = json.loads(text)
        return ApiKeyIdentity(
            tenant_id=data["tenant_id"],
            project_id=data["project_id"],
            permissions=data["permissions"],
        )
    except (ValueError, KeyError, TypeError):
        return None


def require_write_scope(identity: ApiKeyIdentity) -> None:
    if identity.permissions not in ("write", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "insufficient_scope", "required": "write"},
        )


async def check_rate_limit(redis: aioredis.Redis, tenant_id: str) -> None:
    key = f"ratelimit:{tenant_id}:{int(time.time()) // 60}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 120)
    if count > settings.rate_limit_rpm:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "rate_limit_exceeded"},
        )
