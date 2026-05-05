"""
FastAPI dependencies for the auth-service.

`get_current_tenant` validates an Authorization: Bearer <jwt> header and
returns a CurrentTenant context that downstream routes use to scope every
SQL query (WHERE tenant_id = $1). Routes never see the raw JWT.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from core.security import ACCESS_TOKEN_TYPE, decode_token

bearer = HTTPBearer(auto_error=True)


@dataclass(frozen=True)
class CurrentTenant:
    tenant_id: str
    email: str
    role: str


async def get_current_tenant(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> CurrentTenant:
    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token_type"},
        )

    tenant_id = payload.get("tenant_id")
    email = payload.get("email")
    role = payload.get("role", "owner")
    if not tenant_id or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "malformed_token"},
        )

    return CurrentTenant(tenant_id=tenant_id, email=email, role=role)
