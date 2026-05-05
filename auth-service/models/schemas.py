from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field

Permission = Literal["read", "write", "admin"]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int  # seconds until access_token expires


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class TenantPublic(BaseModel):
    id: str
    name: str
    email: EmailStr
    created_at: datetime


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectPublic(BaseModel):
    id: str
    tenant_id: str
    name: str
    created_at: datetime


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------
class ApiKeyCreate(BaseModel):
    permissions: Permission = "write"
    name: Optional[str] = None  # human label — currently unused but reserved for future


class ApiKeyCreated(BaseModel):
    """Returned exactly once at creation. The raw key is never retrievable again."""
    id: str
    project_id: str
    api_key: str           # full raw key — STORE IT NOW
    key_prefix: str
    permissions: Permission
    created_at: datetime


class ApiKeyPublic(BaseModel):
    id: str
    project_id: str
    key_prefix: str
    permissions: Permission
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime] = None
