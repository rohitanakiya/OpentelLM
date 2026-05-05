"""
/auth/* routes — register, login, refresh, logout.
"""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError

from config import settings
from core.database import get_pool
from core.redis import get_redis
from core.security import (
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from dependencies import CurrentTenant, get_current_tenant
from models.schemas import (
    AccessToken,
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TenantPublic,
    TokenPair,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_KEY = "refresh:{token_hash}"


def _refresh_redis_key(token_hash: str) -> str:
    return f"refresh:{token_hash}"


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
@router.post("/register", response_model=TenantPublic, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest):
    pool = get_pool()
    pw_hash = hash_password(body.password)
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO tenants (name, email, password_hash)
            VALUES ($1, $2, $3)
            RETURNING id::text, name, email, created_at
            """,
            body.name, body.email.lower(), pw_hash,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "email_already_registered"},
        )
    return TenantPublic(**dict(row))


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest):
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT id::text AS id, email, password_hash
        FROM tenants
        WHERE email = $1
        """,
        body.email.lower(),
    )
    if not row or not row["password_hash"] or not verify_password(body.password, row["password_hash"]):
        # Generic message — never leak which half of the credential pair was wrong
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_credentials"},
        )

    return await _issue_token_pair(row["id"], row["email"])


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------
@router.post("/refresh", response_model=AccessToken)
async def refresh(body: RefreshRequest):
    redis = get_redis()
    pool = get_pool()

    try:
        payload = decode_token(body.refresh_token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_refresh_token"},
        )

    if payload.get("type") != REFRESH_TOKEN_TYPE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token_type"},
        )

    token_hash = hash_refresh_token(body.refresh_token)
    stored_tenant = await redis.get(_refresh_redis_key(token_hash))
    if stored_tenant is None:
        # Token has been logged out or expired from Redis
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "refresh_token_revoked"},
        )

    tenant_id = stored_tenant.decode() if isinstance(stored_tenant, (bytes, bytearray)) else stored_tenant
    if tenant_id != payload.get("tenant_id"):
        # Token contents have been tampered with — be safe, reject
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "refresh_token_mismatch"},
        )

    # Look up email so the new access token has it
    row = await pool.fetchrow(
        "SELECT email FROM tenants WHERE id = $1",
        tenant_id,
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "tenant_not_found"},
        )

    access = create_access_token(tenant_id, row["email"])
    return AccessToken(
        access_token=access,
        expires_in=settings.access_token_expire_minutes * 60,
    )


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: LogoutRequest):
    redis = get_redis()
    token_hash = hash_refresh_token(body.refresh_token)
    await redis.delete(_refresh_redis_key(token_hash))
    return None


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------
@router.patch("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    current: CurrentTenant = Depends(get_current_tenant),
):
    pool = get_pool()
    redis = get_redis()

    row = await pool.fetchrow(
        "SELECT password_hash FROM tenants WHERE id = $1",
        current.tenant_id,
    )
    if not row or not row["password_hash"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "tenant_has_no_password"},
        )
    if not verify_password(body.current_password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "current_password_incorrect"},
        )

    new_hash = hash_password(body.new_password)
    await pool.execute(
        "UPDATE tenants SET password_hash = $1 WHERE id = $2",
        new_hash, current.tenant_id,
    )

    # Invalidate every refresh token in Redis. We can't filter by tenant_id
    # at the redis-key level (the key is hashed), so we must walk every
    # refresh:* entry and delete the ones whose value matches this tenant.
    # Use SCAN, never KEYS — KEYS blocks the entire Redis instance.
    cursor = b"0"
    while True:
        cursor, batch = await redis.scan(cursor=cursor, match="refresh:*", count=200)
        if batch:
            # Pipeline the value lookups so we don't pay one round-trip per key.
            pipe = redis.pipeline()
            for key in batch:
                pipe.get(key)
            values = await pipe.execute()

            to_delete = [
                key for key, val in zip(batch, values)
                if val and val.decode() == current.tenant_id
            ]
            if to_delete:
                await redis.delete(*to_delete)

        if cursor in (b"0", 0, "0"):
            break

    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _issue_token_pair(tenant_id: str, email: str) -> TokenPair:
    redis = get_redis()
    access = create_access_token(tenant_id, email)
    refresh_raw, refresh_hash = create_refresh_token(tenant_id)

    await redis.set(
        _refresh_redis_key(refresh_hash),
        tenant_id.encode(),
        ex=settings.refresh_token_expire_days * 86_400,
    )

    return TokenPair(
        access_token=access,
        refresh_token=refresh_raw,
        expires_in=settings.access_token_expire_minutes * 60,
    )
